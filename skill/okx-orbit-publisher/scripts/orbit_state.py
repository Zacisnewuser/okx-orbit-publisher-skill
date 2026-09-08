#!/usr/bin/env python3
"""Local state only. No browser, scheduler, network, credentials or public writes."""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import shutil
import time
import unicodedata
import uuid
from datetime import datetime
from urllib.parse import urlsplit, unquote
from zoneinfo import ZoneInfo

VERSION = 1
KINDS = ('post', 'comment', 'like', 'follow_back', 'unfollow')
DEFAULT = {
    'mode': 'dry-run', 'timezone': 'Asia/Shanghai', 'account_id': '',
    'legacy_project_path': '', 'allowed_orbit_prefixes': [],
    'features': ['post', 'comment', 'like'],
    'daily_limits': {'post': 2, 'comment': 20, 'like': 20, 'follow_back': 5, 'unfollow': 5},
    'cooldown_minutes': 30,
    'content': {'language': 'zh-CN', 'persona': '', 'banned_topics': [],
                'post_mode': 'ai', 'comment_mode': 'ai', 'post_prompt': '',
                'comment_prompt': '', 'post_text': '', 'comment_text': '',
                'comment_prefix': '', 'comment_suffix': ''},
    'lists': {'blacklist': [], 'whitelist': [], 'protected': []},
    'discovery': {'candidate_target': 25, 'reserve': 5, 'screen_cap': 240},
    'schedule': {'enabled': False, 'post_times': [], 'active_hours': [], 'registrations': []},
}

def require(condition, message):
    if not condition:
        raise ValueError(message)

def read_json(path):
    def reject_constant(value):
        raise ValueError('Non-finite JSON number: ' + value)
    with open(path, encoding='utf-8') as stream:
        value = json.load(stream, parse_constant=reject_constant)
    require(isinstance(value, dict), 'Input must be a JSON object')
    return value

def merge(base, patch):
    require(isinstance(patch, dict), 'Settings must be an object')
    result = copy.deepcopy(base)
    for key, value in patch.items():
        require(key in base, 'Unknown setting: ' + key)
        result[key] = merge(base[key], value) if isinstance(base[key], dict) else value
    return result

def validate(c):
    require(c['mode'] in ('dry-run', 'confirm', 'auto'), 'Invalid mode')
    ZoneInfo(c['timezone'])
    require(isinstance(c['account_id'], str), 'account_id must be a string')
    require(isinstance(c['legacy_project_path'], str), 'Invalid legacy path')
    require(isinstance(c['features'], list) and set(c['features']) <= set(KINDS), 'Invalid features')
    for value in c['daily_limits'].values():
        require(type(value) is int and 0 <= value <= 1000, 'Invalid daily limit')
    require(type(c['cooldown_minutes']) in (int, float) and math.isfinite(c['cooldown_minutes']) and c['cooldown_minutes'] >= 0, 'Invalid cooldown')
    for key, value in c['content'].items():
        if key == 'banned_topics':
            require(isinstance(value, list) and all(isinstance(x, str) for x in value), 'Invalid banned topics')
        else:
            require(isinstance(value, str), 'Invalid content setting: ' + key)
    for key in ('post_mode', 'comment_mode'):
        require(c['content'][key] in ('fixed', 'ai', 'mixed'), 'Invalid content mode')
    for value in c['lists'].values():
        require(isinstance(value, list) and all(isinstance(x, str) for x in value), 'Invalid account list')
    for value in c['discovery'].values():
        require(type(value) is int and value >= 0, 'Invalid search budget')
    require(type(c['schedule']['enabled']) is bool, 'Invalid schedule enabled flag')
    for key in ('post_times', 'active_hours', 'registrations'):
        require(isinstance(c['schedule'][key], list), 'Invalid schedule setting')
    def valid_time(value):
        return isinstance(value, str) and re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value)
    require(all(valid_time(t) for t in c['schedule']['post_times']), 'Post times must use HH:MM')
    require(all(isinstance(w, list) and len(w) == 2 and all(valid_time(t) for t in w)
                for w in c['schedule']['active_hours']), 'Active hours must contain [start,end] HH:MM pairs')
    require(all(isinstance(r, dict) and all(isinstance(r.get(k), str) and r[k].strip()
                for k in ('provider', 'id', 'function', 'status'))
                for r in c['schedule']['registrations']), 'Invalid scheduler registration')
    require(isinstance(c['allowed_orbit_prefixes'], list), 'Invalid URL list')
    for prefix in c['allowed_orbit_prefixes']:
        safe_url(prefix)

def safe_url(url):
    require(isinstance(url, str) and url and not any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url), 'URL contains whitespace or control characters')
    p = urlsplit(url)
    require(p.scheme == 'https' and p.hostname in ('www.okx.com', 'okx.com') and
            p.port in (None, 443) and not p.username and not p.password, 'URL must use official OKX HTTPS')
    decoded = unquote(p.path)
    require('%' not in decoded and '\\' not in decoded and '//' not in decoded and
            not any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in decoded) and
            all(x not in ('.', '..') for x in decoded.split('/')), 'Ambiguous URL path')
    # Only the Orbit namespace, optionally following a locale segment, is eligible.
    require(re.match(r'^/(?:[a-z]{2}(?:-[a-z]{2,4})?/)?orbit(?:/|$)', decoded), 'URL is outside Orbit')
    blocked = {'trade', 'trading', 'assets', 'balance', 'deposit', 'withdraw', 'withdrawal',
               'orders', 'positions', 'settings', 'account', 'transfer'}
    require(not blocked.intersection(decoded.lower().split('/')), 'Forbidden URL area')
    require(not p.query and not p.fragment, 'Use a canonical URL without query or fragment')
    return p.hostname, decoded.rstrip('/')

def allowed(url, config):
    host, path = safe_url(url)
    for prefix in config['allowed_orbit_prefixes']:
        phost, ppath = safe_url(prefix)
        if host == phost and (path == ppath or path.startswith(ppath + '/')):
            return True
    return False

class Store:
    def __init__(self, root, create=False):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / 'state.sqlite3'
        require(create or self.path.exists(), 'State not initialized; run init')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        # Schema, defaults and version are committed together, including concurrent init.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            require(version in (0, VERSION), 'Unsupported state version; use a compatible Skill, do not reset')
            if version == 0:
                require(create, 'Unversioned database; restore or inspect it, do not overwrite')
                require(not self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone(), 'Refusing to initialize an existing unversioned database')
                self.db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                self.db.execute('''CREATE TABLE tasks(id TEXT PRIMARY KEY, kind TEXT, target TEXT, author TEXT,
              text TEXT, fingerprint TEXT, url TEXT, source TEXT, tag TEXT,
              status TEXT, revision INTEGER, created REAL, expires REAL, started REAL,
              finished REAL, evidence TEXT, object_id TEXT)''')
                self.db.execute('CREATE TABLE events(at REAL, action TEXT, detail TEXT)')
                self.put('config', DEFAULT)
                self.put('paused', True)
                self.put('pause_reason', 'First use: preview before enabling public operations')
                self.put('revision', 1)
                self.db.execute('PRAGMA user_version=1')
            require(isinstance(self.get('config'), dict) and type(self.get('paused')) is bool and
                    type(self.get('revision')) is int, 'Incomplete state metadata; restore a valid backup')
            validate(self.get('config'))
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.db.close()
            raise

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def event(self, action, detail):
        self.db.execute('INSERT INTO events VALUES(?,?,?)', (time.time(), action, json.dumps(detail, ensure_ascii=False)))

    def tasks(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM tasks ORDER BY created')]

    def task(self, task_id):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        require(row is not None, 'Task not found')
        return dict(row)

    def dispatch(self, a):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            # Evaluate expiring permissions after the transaction lock is acquired.
            result = self.run(a, time.time())
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def run(self, a, now):
        c = self.get('config')
        revision = self.get('revision')
        cmd = a.command
        if cmd in ('init', 'status', 'doctor', 'export'):
            result = {'schema_version': VERSION, 'data_dir': str(self.root), 'paused': self.get('paused'),
                      'pause_reason': self.get('pause_reason'), 'revision': revision,
                      'config': c, 'tasks': self.tasks(), 'preflight': self.get('preflight'),
                      'lease': self.get('lease'),
                      'pending_checks': {r['key'][7:]: json.loads(r['value']) for r in self.db.execute("SELECT * FROM meta WHERE key LIKE 'checks:%'")}}
            if cmd == 'doctor':
                result['database_integrity'] = self.db.execute('PRAGMA integrity_check').fetchone()[0]
                result['public_blockers'] = [t['id'] for t in result['tasks'] if t['status'] in ('executing', 'unknown')]
                result['external_checks'] = 'Browser login, CAPTCHA, scheduler and sleep history require host tools'
            if cmd == 'export':
                result['events'] = [dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY at')]
            return result
        if cmd == 'configure':
            patch = read_json(a.file)
            updated = merge(c, patch)
            validate(updated)
            if updated == c:
                return {'changed': False, 'revision': revision}
            require(not any(t['status'] in ('executing', 'unknown') for t in self.tasks()) or
                    updated['account_id'] == c['account_id'], 'Cannot change account with unresolved task')
            # One shared store operates one account. A different account needs a different store.
            require(not c['account_id'] or updated['account_id'] == c['account_id'], 'Use a separate data directory for a different account')
            self.put('config', updated)
            self.put('revision', revision + 1)
            self.put('preflight', None)
            self.db.execute("UPDATE tasks SET status='draft' WHERE status='approved'")
            self.event(cmd, {'revision': revision + 1, 'fields': list(patch)})
            return {'changed': True, 'revision': revision + 1, 'approvals_invalidated': True}
        if cmd == 'pause':
            self.put('paused', True)
            self.put('pause_reason', a.reason)
            self.put('preflight', None)
            self.event(cmd, a.reason)
            return {'paused': True, 'external_scheduler_pause': 'Agent must verify each registered scheduler'}
        if cmd == 'preflight':
            p = read_json(a.file)
            require(c['account_id'] and p.get('account_id') == c['account_id'], 'Account mismatch')
            require(all(p.get(k) is True for k in ('browser_readable', 'logged_in', 'no_challenge', 'dry_run_passed')), 'Preflight incomplete')
            require(isinstance(p.get('evidence'), str) and p['evidence'].strip(), 'Preflight evidence required')
            require(not c['legacy_project_path'] or p.get('legacy_history_checked') is True,
                    'Read and reconcile legacy deduplication history before preflight')
            require(allowed(p['url'], c), 'Preflight URL not allowed')
            p.update({'checked_at': now, 'expires_at': now + 300, 'revision': revision})
            self.put('preflight', p)
            self.event(cmd, p)
            return {'preflight_expires_at': p['expires_at']}
        if cmd == 'resume':
            self.check_preflight(c, revision, now)
            require(c['mode'] in ('confirm', 'auto'), 'Choose confirm or auto after preview')
            require(not any(t['status'] in ('executing', 'unknown') for t in self.tasks()), 'Resolve prior submission first')
            self.put('paused', False)
            self.put('pause_reason', '')
            self.event(cmd, {'user_request': a.reason})
            return {'paused': False, 'mode': c['mode'], 'scheduler': 'Not changed by this tool'}
        if cmd in ('acquire', 'renew', 'release'):
            lease = self.get('lease') or {}
            if cmd == 'acquire':
                require(not lease or lease['expires_at'] <= now, 'Browser session is held by another Agent')
                owner = str(uuid.uuid4())
                self.put('lease', {'owner': owner, 'expires_at': now + 120})
                return {'owner': owner, 'expires_at': now + 120}
            require(lease.get('owner') == a.owner, 'Session owner mismatch')
            if cmd == 'renew':
                require(lease['expires_at'] > now, 'Lease expired; acquire a new session')
                lease['expires_at'] = now + 120
                self.put('lease', lease)
            else:
                self.put('lease', None)
            return {'session': cmd}
        if cmd == 'enqueue':
            p = read_json(a.file)
            kind = p['kind']
            require(kind in KINDS, 'Invalid action')
            require(c['account_id'], 'Set public account ID first')
            require(allowed(p['url'], c), 'Target URL not allowed')
            body = p.get('text', '')
            require(isinstance(body, str) and (kind not in ('post', 'comment') or body.strip()), 'Content required')
            require(isinstance(p.get('target_id'), str) and p['target_id'], 'Stable target ID required')
            if kind != 'post':
                require(isinstance(p.get('author_id'), str) and p['author_id'], 'Stable author ID required')
            normalized = ' '.join(unicodedata.normalize('NFKC', body).split()).casefold()
            fp = hashlib.sha256((c['account_id'] + '|' + kind + '|' + normalized).encode()).hexdigest()
            # Only never-attempted drafts may expire. Keep uncertainty and sent history.
            self.db.execute("UPDATE tasks SET status='expired' WHERE status IN ('draft','approved') AND started IS NULL AND expires<=?", (now,))
            for t in self.tasks():
                if t['status'] in ('not_submitted', 'cancelled', 'expired'):
                    continue
                require(not (t['kind'] == kind and (t['target'] == p['target_id'] if kind != 'post' else False)), 'Target already queued or processed')
                require(kind not in ('post', 'comment') or t['fingerprint'] != fp, 'Duplicate normalized content')
            task_id = str(uuid.uuid4())
            ttl = p.get('ttl_hours', 24)
            require(type(ttl) in (int, float) and 0 < ttl <= 72, 'TTL must be in (0,72] hours')
            self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (task_id, kind, p['target_id'], p.get('author_id', ''), body, fp, p['url'], p.get('source', ''),
                 p.get('tag', ''), 'draft', revision, now, now + ttl * 3600, None, None, None, None))
            self.event(cmd, {'id': task_id})
            return {'id': task_id, 'status': 'draft'}
        task = self.task(a.id)
        if cmd == 'cancel':
            require(task['status'] in ('draft', 'approved') and task['started'] is None, 'Cannot cancel an attempted or terminal task')
            self.db.execute("UPDATE tasks SET status='cancelled',finished=?,evidence=? WHERE id=?", (now, a.reason, a.id))
            self.event(cmd, {'id': a.id, 'reason': a.reason})
            return {'id': a.id, 'status': 'cancelled'}
        if cmd == 'observe':
            require(task['status'] == 'verified', 'Only verified tasks have outcome checks')
            observation = read_json(a.file)
            hours = observation.get('hours')
            require(hours in (24, 72), 'Only 24/72 hour checkpoints supported')
            require(isinstance(observation.get('evidence'), str) and observation['evidence'].strip(), 'Observation evidence required')
            require(isinstance(observation.get('metrics'), dict), 'Metrics object required; missing values use unavailable')
            checks = self.get('checks:' + a.id, [])
            check = next((x for x in checks if x['hours'] == hours), None)
            require(check and check['status'] == 'pending' and now >= check['due_at'], 'Checkpoint not due or already completed')
            check.update({'status': 'observed', 'observed_at': now, 'delay_seconds': now - check['due_at'],
                          'evidence': observation['evidence'], 'metrics': observation['metrics']})
            self.put('checks:' + a.id, checks)
            self.event(cmd, {'id': a.id, 'hours': hours})
            return check
        if cmd == 'approve':
            require(task['status'] == 'draft' and task['expires'] > now, 'Task is not a current draft')
            require(c['mode'] in ('confirm', 'auto'), 'Dry-run tasks cannot be approved')
            self.db.execute("UPDATE tasks SET status='approved',revision=? WHERE id=?", (revision, a.id))
            self.event(cmd, {'id': a.id, 'basis': a.reason, 'mode': c['mode']})
            return {'id': a.id, 'status': 'approved'}
        if cmd == 'claim':
            lease = self.get('lease') or {}
            require(lease.get('owner') == a.owner and lease['expires_at'] > now, 'Acquire a live browser session first')
            require(not self.get('paused') and c['mode'] != 'dry-run', 'Paused or dry-run')
            require(task['status'] == 'approved' and task['revision'] == revision and task['expires'] > now, 'Task needs fresh approval')
            require(task['kind'] in c['features'], 'Feature disabled')
            require(allowed(task['url'], c), 'URL no longer allowed')
            require(task['author'] not in c['lists']['blacklist'], 'Blacklisted target')
            require(task['kind'] != 'unfollow' or task['target'] not in c['lists']['protected'], 'Protected account')
            self.check_preflight(c, revision, now)
            tasks = self.tasks()
            require(not any(t['status'] in ('executing', 'unknown') for t in tasks), 'Unresolved submission blocks new writes')
            started = [t for t in tasks if t['started'] is not None]
            require(not started or now - max(t['started'] for t in started) >= c['cooldown_minutes'] * 60, 'Cooldown active')
            day = datetime.fromtimestamp(now, ZoneInfo(c['timezone'])).date()
            used = sum(t['kind'] == task['kind'] and datetime.fromtimestamp(t['started'], ZoneInfo(c['timezone'])).date() == day for t in started)
            require(used < c['daily_limits'][task['kind']], 'Daily attempt limit reached')
            self.db.execute("UPDATE tasks SET status='executing',started=? WHERE id=?", (now, a.id))
            self.event(cmd, {'id': a.id, 'owner': a.owner})
            return {'id': a.id, 'status': 'executing', 'submit_once': True}
        if cmd == 'finish':
            require(task['status'] == 'executing', 'Only executing task can finish')
            require(a.result != 'verified' or a.object_id, 'Verified result needs stable public object ID')
            return self.finish(task, a.result, a.reason, a.object_id, now)
        if cmd == 'resolve-verified':
            require(task['status'] in ('executing', 'unknown'), 'Task is not unresolved')
            return self.finish(task, 'verified', a.reason, a.object_id, now)
        if cmd == 'resolve-absent':
            require(task['status'] in ('executing', 'unknown'), 'Task is not unresolved')
            return self.finish(task, 'not_submitted', 'USER_CONFIRMED_ABSENT: ' + a.reason, None, now)
        raise ValueError('Unknown command')

    def check_preflight(self, c, revision, now):
        p = self.get('preflight') or {}
        require(p.get('revision') == revision and p.get('expires_at', 0) > now and
                p.get('account_id') == c['account_id'], 'Fresh browser and preview preflight required')

    def finish(self, task, result, evidence, object_id, now):
        require(result != 'verified' or isinstance(object_id, str) and object_id.strip(), 'Verified result needs non-empty public object ID')
        self.db.execute('UPDATE tasks SET status=?,finished=?,evidence=?,object_id=? WHERE id=?',
                        (result, now, evidence, object_id, task['id']))
        self.event('finish', {'id': task['id'], 'result': result, 'evidence': evidence})
        if result == 'verified':
            self.put('checks:' + task['id'], [{'hours': h, 'due_at': task['started'] + h * 3600, 'status': 'pending'} for h in (24, 72)])
        return {'id': task['id'], 'status': result}

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', default=os.environ.get('OKX_ORBIT_DATA_DIR', str(Path.home() / 'Library/Application Support/okx-orbit-publisher')))
    sub = p.add_subparsers(dest='command', required=True)
    for cmd in ('init', 'status', 'doctor', 'export', 'acquire'):
        sub.add_parser(cmd)
    for cmd in ('configure', 'preflight', 'enqueue'):
        sub.add_parser(cmd).add_argument('--file', required=True)
    for cmd in ('pause', 'resume'):
        sub.add_parser(cmd).add_argument('--reason', required=True)
    for cmd in ('renew', 'release'):
        sub.add_parser(cmd).add_argument('--owner', required=True)
    s = sub.add_parser('claim')
    s.add_argument('--id', required=True)
    s.add_argument('--owner', required=True)
    s = sub.add_parser('observe')
    s.add_argument('--id', required=True)
    s.add_argument('--file', required=True)
    for cmd in ('approve', 'finish', 'resolve-verified', 'resolve-absent', 'cancel'):
        s = sub.add_parser(cmd)
        s.add_argument('--id', required=True)
        s.add_argument('--reason', required=True)
        if cmd == 'finish':
            s.add_argument('--result', choices=('verified', 'unknown', 'not_submitted'), required=True)
            s.add_argument('--object-id')
        if cmd == 'resolve-verified':
            s.add_argument('--object-id', required=True)
    sub.add_parser('backup').add_argument('--output', required=True)
    sub.add_parser('restore').add_argument('--file', required=True)
    return p

def main():
    a = parser().parse_args()
    if hasattr(a, 'reason'):
        require(a.reason.strip(), 'A concrete reason or evidence is required')
    os.umask(0o077)
    if a.command == 'restore':
        root = Path(a.data_dir).expanduser().resolve()
        require(not root.exists(), 'Restore requires a new data directory; never overwrite live state')
        source = Path(a.file).resolve()
        require(source.is_file(), 'Backup does not exist')
        src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
        require(src.execute('PRAGMA user_version').fetchone()[0] == VERSION and
                src.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Invalid backup')
        root.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.orbit-restore-', dir=str(root.parent)))
        try:
            dst = sqlite3.connect(str(stage / 'state.sqlite3'))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            state = Store(stage)
            try:
                result = state.dispatch(argparse.Namespace(command='pause', reason='Restored backup: reconcile newer public actions before resuming'))
            finally:
                state.db.close()
            require(not root.exists(), 'Restore target appeared during restore; refusing overwrite')
            os.rename(stage, root)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    else:
        state = Store(a.data_dir, create=a.command == 'init')
        if a.command == 'backup':
            output = Path(a.output).expanduser().resolve()
            require(not output.exists(), 'Backup destination already exists')
            output.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(output), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            dst = sqlite3.connect(str(output))
            state.db.backup(dst)
            dst.close()
            result = {'backup': str(output), 'scope': 'SQLite config, tasks, audit, pause and pending checks; copy optional sidecar files separately'}
        else:
            result = state.dispatch(a)
    state.db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
