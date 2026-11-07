"""Check imported source hashes and keep private/generated inputs outside Git."""
from pathlib import Path
import hashlib,json,re,subprocess
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'SOURCE_BASELINE.json').read_text())
for row in manifest['files']:
    p=root/row['path']
    assert p.resolve().is_relative_to(root.resolve()) and p.is_file(),row['path']
    assert hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256'],row['path']
for name in subprocess.check_output(['git','-C',str(root),'ls-files'],text=True).splitlines():
    p=root/name
    assert p.suffix not in {'.apk','.zip','.img','.pk8','.jks','.p12','.vhdx'},name
    assert not re.search(rb'(?m)^-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{22,}',p.read_bytes()),name
print('PASS:',len(manifest['files']),'imported source files; no generated/private inputs tracked')
