#!/usr/bin/env python3
"""Build a deterministic Skill-only ZIP plus license and checksum; never include state."""
import hashlib
from pathlib import Path
import re
import zipfile
from check_release import check, check_archive, ROOT, SKILL


def build():
    _, errors = check()
    if errors:
        raise ValueError('\n'.join(errors))
    version = re.search(r'  version: "(\d+\.\d+\.\d+)"', (SKILL / 'SKILL.md').read_text()).group(1)
    paths = [SKILL / 'SKILL.md', SKILL / 'agents/openai.yaml']
    paths += sorted((SKILL / 'references').glob('*.md'))
    paths += sorted((SKILL / 'scripts').glob('*.py'))
    entries = [(p, 'okx-orbit-publisher/' + p.relative_to(SKILL).as_posix()) for p in paths]
    entries.append((ROOT / 'LICENSE', 'okx-orbit-publisher/LICENSE'))
    (ROOT / 'dist').mkdir(exist_ok=True)
    output = ROOT / 'dist' / ('okx-orbit-publisher-' + version + '.zip')
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in sorted(entries, key=lambda x: x[1]):
            if path.is_symlink():
                raise ValueError('Cannot package a symlink: ' + name)
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    errors = check_archive(output)
    if errors:
        raise ValueError('\n'.join(errors))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(digest + '  ' + output.name + '\n')
    print(output)
    print(digest)
    return output


if __name__ == '__main__':
    build()
