"""Synthetic privacy regressions; never use real personal data as a fixture."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location('privacy_check', Path(__file__).resolve().parents[1] / 'tools/check_release.py')
SCAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCAN)


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args, email=None):
        env = dict(os.environ)
        address = email or ('123+fixture' + '@users.noreply.github.com')
        env.update(GIT_AUTHOR_NAME='fixture', GIT_COMMITTER_NAME='fixture',
                   GIT_AUTHOR_EMAIL=address, GIT_COMMITTER_EMAIL=address)
        return subprocess.check_output(['git', '-C', str(self.root), *args], env=env, stderr=subprocess.PIPE)

    def init(self):
        self.git('init')
        (self.root / 'README.md').write_text('Synthetic public content\n')
        self.git('add', 'README.md')

    def test_private_values_and_allowed_public_identity(self):
        self.assertEqual(SCAN.private_patterns('123+fixture' + '@users.noreply.github.com'), [])
        self.assertEqual(SCAN.private_patterns('help' + '@example.com'), [])
        for text in ('person' + '@private.test', '/Users/' + 'fixture/Documents/',
                     'ghp_' + 'a' * 36, '9' * 18,
                     'password=' + '"' + 'synthetic-value-123' + '"'):
            self.assertTrue(SCAN.private_patterns(text))

    def test_git_checks_author_and_committer_metadata(self):
        self.init()
        self.git('commit', '-m', 'Synthetic commit', email='person' + '@private.test')
        errors = SCAN.check_git(self.root)
        self.assertTrue(any('metadata' in e for e in errors))
        self.assertNotIn('private.test', '\n'.join(errors))

    def test_git_checks_old_blob_after_latest_file_is_clean(self):
        self.init()
        (self.root / 'README.md').write_text('person' + '@private.test')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'Synthetic historical data')
        (self.root / 'README.md').write_text('Now clean\n')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'Clean current file')
        self.assertTrue(any('blob' in e for e in SCAN.check_git(self.root)))

    def test_clean_git_history_passes(self):
        self.init()
        self.git('commit', '-m', 'Clean synthetic fixture')
        self.assertEqual(SCAN.check_git(self.root), [])

    def test_archive_checks_content_and_metadata(self):
        path = self.root / 'fixture.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('okx-orbit-publisher/SKILL.md', 'person' + '@private.test')
            archive.comment = ('person' + '@private.test').encode()
        errors = SCAN.check_archive(path)
        self.assertIn('Private archive member content', errors)
        self.assertIn('Private archive comment', errors)

    def test_archive_rejects_runtime_data_and_traversal(self):
        path = self.root / 'fixture.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('okx-orbit-publisher/state/state.sqlite3', 'fixture')
            archive.writestr('okx-orbit-publisher/../outside.md', 'fixture')
        self.assertTrue(SCAN.check_archive(path))


if __name__ == '__main__':
    unittest.main()
