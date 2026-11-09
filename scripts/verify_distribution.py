"""Validate wheel/sdist metadata and keep private runtime files out of releases."""
from pathlib import Path
import hashlib
import json
import re
import sys
import tarfile
import zipfile


def verify(directory):
    artifacts = sorted(Path(directory).glob('*.whl')) + sorted(Path(directory).glob('*.tar.gz'))
    if len(artifacts) != 2:
        raise ValueError('Expected one wheel and one source distribution')
    records = []
    for artifact in artifacts:
        if artifact.suffix == '.whl':
            with zipfile.ZipFile(artifact) as archive:
                files = {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}
            required = ['x_list_discovery/cli.py', 'x_list_discovery/pipeline.py']
        else:
            with tarfile.open(artifact) as archive:
                files = {member.name.split('/', 1)[-1]: archive.extractfile(member).read()
                         for member in archive.getmembers() if member.isfile()}
            required = ['pyproject.toml', 'LICENSE', 'README.md', 'README.zh-CN.md',
                        'examples/members.csv', 'examples/followings.json', 'src/x_list_discovery/cli.py']
        if any(name not in files for name in required):
            raise ValueError('Incomplete distribution: ' + artifact.name)
        if not any('LICENSE' in name for name in files):
            raise ValueError('Missing license: ' + artifact.name)
        for name, data in files.items():
            parts = Path(name).parts
            if any(p in {'runs', 'results', 'legacy', '.venv', '.git', '__pycache__'} for p in parts):
                raise ValueError('Runtime/private path in distribution: ' + name)
            if Path(name).name.startswith('.env') and Path(name).name != '.env.example':
                raise ValueError('Credential file in distribution: ' + name)
            if name.endswith(('.xlsx', '.pyc')):
                raise ValueError('Unexpected generated file: ' + name)
            if re.search(rb'/(?:Users|home)/[A-Za-z0-9._-]+/', data):
                raise ValueError('Private project path in distribution: ' + name)
        records.append({'file': artifact.name, 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                        'bytes': artifact.stat().st_size, 'files': len(files)})
    return records


if __name__ == '__main__':
    print(json.dumps(verify(sys.argv[1] if len(sys.argv) > 1 else 'dist'), indent=2))
