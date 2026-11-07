"""Prepare authentic app-local FileExtender dependencies for optional Camera.

The authentic SDK selects a .qti filename on this Qualcomm device. Preserve the
provider bytes/SONAME and JNI methods; stage that provider under its requested name.
No library is installed and no global linker or vendor partition is changed.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'configs/oem-camera-fileextender-23.2.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(native, output):
    contract = json.loads(CONTRACT.read_text())
    files = {}
    for name, spec in contract['files'].items():
        data = (native / spec['source']).read_bytes()
        if (sha(data) != spec['sha256'] or data[:6] != b'\x7fELF\x02\x01'
                or int.from_bytes(data[18:20], 'little') != 183):
            raise ValueError('unreviewed ARM64 FileExtender dependency: ' + name)
        files[name] = data
    if output.exists():
        raise ValueError('refusing to overwrite prepared FileExtender files')
    output.mkdir(parents=True)
    for name, data in files.items():
        (output / name).write_bytes(data)
    report = dict(schema=1, installable=False,
                  files={n: dict(bytes=len(b), sha256=sha(b)) for n, b in files.items()},
                  scope=contract['scope'], limits=contract['limits'])
    (output / 'provenance.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.native, args.output), indent=2))
