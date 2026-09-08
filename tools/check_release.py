#!/usr/bin/env python3
"""Check the explicit public tree, local Markdown links, and common private artifacts."""
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skill/okx-orbit-publisher'
TOP_FILES = {'README.md', 'LICENSE', 'CONTRIBUTING.md', 'SECURITY.md', 'CHANGELOG.md', '.gitignore'}
TOP_DIRS = {'skill', 'tests', 'tools', 'docs', '.github'}
SKIP = {'.git', 'dist', '__pycache__', '.pytest_cache', '.venv'}


def private_patterns(text):
    """Return categories, never echo a matched private value into CI logs."""
    findings = []
    patterns = {
        'private-key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'github-token': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b',
        'api-key': r'\bsk-[A-Za-z0-9_-]{24,}\b',
        'personal-home-path': r'(?:/Users/|/home/)[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\[^\\\s]+\\',
        'long-account-id': r'(?<![A-Za-z0-9])[0-9]{15,20}(?![A-Za-z0-9])',
        'credential-value': r'''(?i)["']?(?:password|api_key|access_token|refresh_token|cookie)["']?\s*[:=]\s*["'][^"'\s]{12,}["']''',
    }
    for category, pattern in patterns.items():
        if re.search(pattern, text):
            findings.append(category)
    for address in re.findall(r'[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text):
        domain = address.rsplit('@', 1)[1].lower()
        if domain not in {'users.noreply.github.com', 'noreply.github.com', 'example.com', 'example.org', 'example.net'}:
            findings.append('personal-email')
            break
    return findings


def check_git(root, revision='HEAD'):
    """Inspect the exact publishable ancestry, including old blobs and Git identities."""
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)
    tip = git('rev-parse', '--verify', '--end-of-options', revision + '^{commit}').decode().strip()
    errors = []
    commits = git('rev-list', tip).decode().splitlines()
    for commit in commits:
        metadata = git('show', '-s', '--format=%an%n%ae%n%cn%n%ce%n%B', commit).decode('utf-8', 'replace')
        if private_patterns(metadata):
            errors.append('Git commit ' + commit[:12] + ': private metadata detected')
    objects = git('rev-list', '--objects', '--no-object-names', tip).decode().splitlines()
    for oid in objects:
        if git('cat-file', '-t', oid).strip() != b'blob':
            continue
        raw = git('cat-file', '-p', oid)
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            errors.append('Git blob ' + oid[:12] + ': binary history requires manual review')
            continue
        if private_patterns(text):
            errors.append('Git blob ' + oid[:12] + ': private content detected')
    return errors


def check_archive(path):
    errors = []
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            errors.append('Archive integrity check failed')
        names = archive.namelist()
        if len(names) != len(set(names)):
            errors.append('Duplicate archive member')
        for item in archive.infolist():
            parts = Path(item.filename).parts
            if (not parts or parts[0] != 'okx-orbit-publisher' or '..' in parts or
                    any(p in {'.git', 'data', 'state', 'backups', '__pycache__'} for p in parts) or
                    not (item.filename.endswith(('.md', '.py', '.yaml')) or item.filename == 'okx-orbit-publisher/LICENSE')):
                errors.append('Unexpected archive member')
            if private_patterns(item.filename + item.comment.decode('utf-8', 'replace')):
                errors.append('Private archive metadata')
            try:
                content = archive.read(item).decode('utf-8')
                if private_patterns(content):
                    errors.append('Private archive member content')
            except UnicodeDecodeError:
                errors.append('Binary archive member requires manual review')
        if private_patterns(archive.comment.decode('utf-8', 'replace')):
            errors.append('Private archive comment')
    return errors


def check(root=ROOT):
    errors = []
    files = []
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if set(rel.parts).intersection(SKIP):
            continue
        if path.is_symlink():
            errors.append(str(rel) + ': symlink is not a publishable source file')
            continue
        if not path.is_file():
            continue
        if (len(rel.parts) == 1 and rel.name not in TOP_FILES) or (len(rel.parts) > 1 and rel.parts[0] not in TOP_DIRS):
            errors.append(str(rel) + ': outside public allowlist')
            continue
        if path.suffix not in ('.md', '.py', '.yml', '.yaml') and rel.name not in TOP_FILES:
            errors.append(str(rel) + ': unexpected file type')
            continue
        if any(x in rel.parts for x in ('data', 'backups', 'state')) or '.local.' in rel.name:
            errors.append(str(rel) + ': personal state must not be published')
            continue
        text = path.read_text(encoding='utf-8')
        files.append(str(rel))
        if private_patterns(text):
            errors.append(str(rel) + ': possible private content (inspect locally)')
        if path.suffix == '.md':
            for target in re.findall(r'\]\(([^)]+)\)', text):
                if '://' in target or target.startswith('#'):
                    continue
                dest = (path.parent / target.split('#')[0]).resolve()
                if root.resolve() not in dest.parents or not dest.exists():
                    errors.append(str(rel) + ': broken/outside local link: ' + target)
    entry = root / 'skill/okx-orbit-publisher/SKILL.md'
    body = entry.read_text(encoding='utf-8')
    if not body.startswith('---\n') or '\nname: okx-orbit-publisher\n' not in body:
        errors.append('Invalid Skill frontmatter')
    if not re.search(r'  version: "\d+\.\d+\.\d+"', body):
        errors.append('Missing release version')
    if not (root / 'LICENSE').read_text().startswith('MIT License\n'):
        errors.append('Missing MIT license')
    return files, errors


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--git-ref', default='HEAD', help='Exact ancestry intended for publication')
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    files, errors = check()
    if (ROOT / '.git').exists():
        errors += check_git(ROOT, args.git_ref)
    else:
        print('Git history unavailable: source-only check; not a complete publication audit.')
    if args.archive:
        errors += check_archive(args.archive)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        sys.exit(1)
    print('PASS: %d source files; selected Git ancestry and optional archive checked where available.' % len(files))
    print('This heuristic check does not replace manual review of the complete Git diff.')
