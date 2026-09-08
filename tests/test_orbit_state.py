"""Offline state contract tests: never connect to OKX or a real user database."""
import json
import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'skill/okx-orbit-publisher/scripts/orbit_state.py'
URL = 'https://www.okx.com/zh-hans/orbit/post/test'


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'user-state'
        self.counter = 0
        self.call('init')

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *args, data=None):
        return [sys.executable, str(SCRIPT), '--data-dir', str(data or self.data), *args]

    def call(self, *args, ok=True, data=None):
        r = subprocess.run(self.command(*args, data=data), capture_output=True, text=True)
        self.assertEqual(r.returncode == 0, ok, r.stdout + r.stderr)
        return json.loads(r.stdout if r.returncode == 0 else r.stderr)

    def file(self, value):
        self.counter += 1
        p = self.root / ('input-%d.json' % self.counter)
        p.write_text(json.dumps(value), encoding='utf-8')
        return str(p)

    def configure(self, patch):
        return self.call('configure', '--file', self.file(patch))

    def ready(self):
        self.configure({'account_id': 'test-account', 'mode': 'confirm', 'cooldown_minutes': 0,
                        'allowed_orbit_prefixes': ['https://www.okx.com/zh-hans/orbit']})
        self.preflight()
        self.call('resume', '--reason', 'Test user requests execution')

    def preflight(self):
        return self.call('preflight', '--file', self.file({'account_id': 'test-account', 'url': URL,
            'browser_readable': True, 'logged_in': True, 'no_challenge': True,
            'dry_run_passed': True, 'evidence': 'Offline fixture, no browser used'}))

    def task(self, target='p1', text='First unique comment', kind='comment', **kwargs):
        return self.call('enqueue', '--file', self.file({'kind': kind, 'target_id': target,
            'author_id': 'author-' + target, 'url': URL, 'text': text, **kwargs}))['id']

    def approve(self, task_id):
        self.call('approve', '--id', task_id, '--reason', 'Specific offline approval')

    def owner(self):
        return self.call('acquire')['owner']

    def test_default_and_reinit_preserve_settings(self):
        initial = self.call('status')
        self.assertTrue(initial['paused'])
        self.assertEqual(initial['config']['mode'], 'dry-run')
        self.configure({'content': {'persona': 'Friendly'}, 'schedule': {'post_times': ['20:00']}})
        state = self.call('init')
        self.assertEqual(state['config']['content']['persona'], 'Friendly')
        self.assertEqual(state['config']['schedule']['post_times'], ['20:00'])
        self.assertEqual(state['config']['daily_limits']['comment'], 20)

    def test_pause_blocks_claim_even_with_prior_approval(self):
        self.ready()
        task_id = self.task()
        self.approve(task_id)
        owner = self.owner()
        self.call('pause', '--reason', 'Face verification unavailable')
        self.call('claim', '--id', task_id, '--owner', owner, ok=False)
        self.assertEqual(self.call('status')['tasks'][0]['status'], 'approved')

    def test_changed_settings_invalidate_approval(self):
        self.ready()
        task_id = self.task()
        self.approve(task_id)
        self.configure({'content': {'comment_prompt': 'Shorter'}})
        state = self.call('status')
        self.assertEqual(state['tasks'][0]['status'], 'draft')
        self.assertIsNone(state['preflight'])

    def test_concurrent_agents_cannot_claim_twice(self):
        self.ready()
        first = self.task()
        second = self.task('p2', 'Second unique comment')
        self.approve(first)
        self.approve(second)
        owner = self.owner()
        self.call('acquire', ok=False)
        procs = [subprocess.Popen(self.command('claim', '--id', tid, '--owner', owner),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for tid in (first, second)]
        for proc in procs:
            proc.communicate(timeout=10)
        self.assertEqual(sorted(p.returncode for p in procs), [0, 1])
        self.assertEqual(sum(t['status'] == 'executing' for t in self.call('status')['tasks']), 1)

    def test_unknown_survives_restart_and_requires_resolution(self):
        self.ready()
        first = self.task()
        self.approve(first)
        owner = self.owner()
        self.call('claim', '--id', first, '--owner', owner)
        self.call('finish', '--id', first, '--result', 'unknown', '--reason', 'Interrupted after click')
        second = self.task('p2', 'Another comment')
        self.approve(second)
        self.call('claim', '--id', second, '--owner', owner, ok=False)
        self.call('resolve-verified', '--id', first, '--object-id', 'public-comment-1', '--reason', 'Public fixture matched')
        self.call('claim', '--id', second, '--owner', owner)
        self.assertIn(first, self.call('export')['pending_checks'])

    def test_normalized_duplicates_and_url_guards(self):
        self.ready()
        self.task(text='Hello   WORLD')
        self.call('enqueue', '--file', self.file({'kind': 'comment', 'target_id': 'p2',
            'author_id': 'a2', 'url': URL, 'text': 'hello world'}), ok=False)
        for url in ('https://www.okx.com/', 'https://www.okx.com/trade',
                    'https://www.okx.com/zh-hans/orbit/../trade', 'https://www.okx.com/orbit/settings',
                    'https://www.okx.com.evil.test/orbit', 'https://www.okx.com/orbit?redirect=/trade',
                    'https://www.okx.com/orbit/%252e%252e/trade'):
            self.call('configure', '--file', self.file({'allowed_orbit_prefixes': [url]}), ok=False)

    def test_backup_restore_preserve_history_but_pause(self):
        self.ready()
        task_id = self.task()
        self.approve(task_id)
        owner = self.owner()
        self.call('claim', '--id', task_id, '--owner', owner)
        backup = self.root / 'backup.sqlite3'
        self.call('backup', '--output', str(backup))
        self.call('backup', '--output', str(backup), ok=False)
        restored = self.root / 'restored'
        self.call('restore', '--file', str(backup), data=restored)
        state = self.call('status', data=restored)
        self.assertTrue(state['paused'])
        self.assertIsNone(state['preflight'])
        self.assertEqual(state['tasks'][0]['status'], 'executing')
        self.assertEqual(state['config']['account_id'], 'test-account')
        self.call('restore', '--file', str(backup), data=restored, ok=False)

    def test_cooldown_and_daily_limit(self):
        self.ready()
        self.configure({'daily_limits': {'comment': 1}, 'cooldown_minutes': 30})
        self.preflight()
        first = self.task()
        self.approve(first)
        owner = self.owner()
        self.call('claim', '--id', first, '--owner', owner)
        self.call('finish', '--id', first, '--result', 'verified', '--object-id', 'c1', '--reason', 'Matched')
        second = self.task('p2', 'Second comment')
        self.approve(second)
        self.assertIn('Cooldown', self.call('claim', '--id', second, '--owner', owner, ok=False)['error'])
        self.configure({'cooldown_minutes': 0})
        self.preflight()
        self.approve(second)
        self.assertIn('Daily', self.call('claim', '--id', second, '--owner', owner, ok=False)['error'])

    def test_expired_preflight_and_task(self):
        self.ready()
        task_id = self.task(ttl_hours=0.000001)
        time.sleep(.01)
        self.call('approve', '--id', task_id, '--reason', 'Expired', ok=False)
        with closing(sqlite3.connect(self.data / 'state.sqlite3')) as db, db:
            p = json.loads(db.execute("SELECT value FROM meta WHERE key='preflight'").fetchone()[0])
            p['expires_at'] = 0
            db.execute("UPDATE meta SET value=? WHERE key='preflight'", (json.dumps(p),))
        second = self.task('p2', 'Second comment')
        self.approve(second)
        self.assertIn('preflight', self.call('claim', '--id', second, '--owner', self.owner(), ok=False)['error'])

    def test_protected_and_disabled_relationships(self):
        self.ready()
        self.configure({'features': ['unfollow'], 'lists': {'protected': ['protected-id']}})
        self.preflight()
        task_id = self.task('protected-id', '', 'unfollow')
        self.approve(task_id)
        self.assertIn('Protected', self.call('claim', '--id', task_id, '--owner', self.owner(), ok=False)['error'])

    def test_observe_does_not_resubmit_and_records_delay(self):
        self.ready()
        task_id = self.task()
        self.approve(task_id)
        self.call('claim', '--id', task_id, '--owner', self.owner())
        self.call('finish', '--id', task_id, '--result', 'verified', '--object-id', 'c1', '--reason', 'Matched')
        observation = self.file({'hours': 24, 'evidence': 'Read-only fixture', 'metrics': {'likes': 'unavailable'}})
        self.call('observe', '--id', task_id, '--file', observation, ok=False)
        with closing(sqlite3.connect(self.data / 'state.sqlite3')) as db, db:
            key = 'checks:' + task_id
            checks = json.loads(db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()[0])
            checks[0]['due_at'] = time.time() - 60
            db.execute('UPDATE meta SET value=? WHERE key=?', (json.dumps(checks), key))
        result = self.call('observe', '--id', task_id, '--file', observation)
        self.assertGreaterEqual(result['delay_seconds'], 60)
        self.assertEqual(result['metrics']['likes'], 'unavailable')
        self.call('observe', '--id', task_id, '--file', observation, ok=False)
        self.assertEqual(self.call('status')['tasks'][0]['status'], 'verified')

    def test_expired_draft_can_be_replaced_but_sent_history_cannot(self):
        self.ready()
        old = self.task(ttl_hours=0.000001)
        time.sleep(.01)
        new = self.task()
        self.assertNotEqual(old, new)
        self.assertEqual(self.call('status')['tasks'][0]['status'], 'expired')
        self.approve(new)
        self.call('claim', '--id', new, '--owner', self.owner())
        self.call('finish', '--id', new, '--result', 'verified', '--object-id', 'public-c', '--reason', 'Matched fixture')
        self.call('enqueue', '--file', self.file({'kind': 'comment', 'target_id': 'p1',
            'author_id': 'author-p1', 'url': URL, 'text': 'Different wording'}), ok=False)

    def test_cancel_allows_edit_but_not_executing_or_unknown(self):
        self.ready()
        old = self.task()
        self.approve(old)
        self.call('cancel', '--id', old, '--reason', 'User edits draft')
        new = self.task(text='Edited comment')
        self.approve(new)
        self.call('claim', '--id', new, '--owner', self.owner())
        self.call('cancel', '--id', new, '--reason', 'Cannot erase attempt', ok=False)
        self.call('finish', '--id', new, '--result', 'unknown', '--reason', 'No result')
        self.call('cancel', '--id', new, '--reason', 'Cannot erase uncertainty', ok=False)

    def test_concurrent_initialization_is_idempotent(self):
        other = self.root / 'parallel'
        procs = [subprocess.Popen(self.command('init', data=other), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True) for _ in range(3)]
        outputs = [p.communicate(timeout=10) for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0, 0], outputs)
        self.assertTrue(self.call('status', data=other)['paused'])

    def module(self):
        spec = importlib.util.spec_from_file_location('orbit_under_test', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_interrupted_initialization_rolls_back_schema_and_metadata(self):
        module = self.module()
        other = self.root / 'interrupted'
        with mock.patch.object(module.Store, 'put', side_effect=RuntimeError('Power loss')):
            with self.assertRaises(RuntimeError):
                module.Store(other, create=True)
        self.assertTrue(self.call('init', data=other)['paused'])

    def test_restore_failure_never_exposes_running_target(self):
        self.ready()
        backup = self.root / 'active-backup.sqlite3'
        self.call('backup', '--output', str(backup))
        other = self.root / 'failed-restore'
        module = self.module()
        with mock.patch.object(sys, 'argv', self.command('restore', '--file', str(backup), data=other)[1:]):
            with mock.patch.object(module.Store, 'dispatch', side_effect=RuntimeError('Pause write failed')):
                with self.assertRaises(RuntimeError):
                    module.main()
        self.assertFalse(other.exists())
        self.assertEqual(list(self.root.glob('.orbit-restore-*')), [])

    def test_invalid_settings_and_control_characters_fail_closed(self):
        before = self.call('status')['revision']
        for patch in ({'schedule': {'post_times': ['25:99']}},
                      {'schedule': {'active_hours': ['09:00-18:00']}},
                      {'schedule': {'registrations': [{}]}},
                      {'cooldown_minutes': float('inf')}):
            self.call('configure', '--file', self.file(patch), ok=False)
        for url in ('https://www.okx.com/orbit\n', 'https://www.okx.com/orbit/%00post',
                    'https://www.okx.com/orbit//settings'):
            self.call('configure', '--file', self.file({'allowed_orbit_prefixes': [url]}), ok=False)
        self.assertEqual(self.call('status')['revision'], before)

    def test_empty_verified_id_cannot_clear_unknown(self):
        self.ready()
        tid = self.task()
        self.approve(tid)
        self.call('claim', '--id', tid, '--owner', self.owner())
        self.call('finish', '--id', tid, '--result', 'unknown', '--reason', 'No receipt')
        self.call('resolve-verified', '--id', tid, '--object-id', ' ', '--reason', 'Missing ID', ok=False)
        self.assertEqual(self.call('status')['tasks'][0]['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
