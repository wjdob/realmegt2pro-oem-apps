"""Prepare pinned optional filter LUTs and their existing vendor path fixup.

The vendor library is a ROM source input, never an add-on's ODM write target.
Only the separate LUT files belong to the optional Camera install/removal.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'configs/oem-camera-filter-assets-23.2.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def route_assets(data, contract):
    if (sha(data) != contract['native_input_sha256']
            or data[:6] != b'\x7fELF\x02\x01'
            or int.from_bytes(data[18:20], 'little') != 183):
        raise ValueError('unreviewed ARM64 filter implementation')
    old, new = (bytes.fromhex(contract[k]) for k in ('old_hex', 'new_hex'))
    position = contract['prefix_offset']
    if (not old or len(old) != len(new) or data.count(old) != 1
            or data[position:position + len(old)] != old):
        raise ValueError('filter asset prefix preimage differs')
    phoff = struct.unpack_from('<Q', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 54)
    if size != 56 or phoff + size * count > len(data):
        raise ValueError('unexpected filter ELF program headers')
    segments = [struct.unpack_from('<IIQQQQQQ', data, phoff + i * size)
                for i in range(count)]
    owners = [s for s in segments if s[0] == 1
              and s[2] <= position and position + len(old) <= s[2] + s[5]]
    if len(owners) != 1 or owners[0][1] != 4:
        raise ValueError('filter prefix must be in a read-only non-executable segment')
    result = data[:position] + new + data[position + len(old):]
    if sha(result) != contract['native_output_sha256']:
        raise ValueError('canonical filter asset route differs')
    return result


def prepare(native, assets, output):
    contract = json.loads(CONTRACT.read_text())
    routed = route_assets(native.read_bytes(), contract)
    specs = contract['files']
    if set(p.name for p in assets.iterdir()) != set(specs):
        raise ValueError('filter asset inventory differs')
    files = {}
    for name, spec in specs.items():
        if not re.fullmatch(r'[A-Za-z0-9_+-][A-Za-z0-9_.+-]*', name):
            raise ValueError('unsafe filter asset name')
        source = assets / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('filter asset must be a regular file')
        data = source.read_bytes()
        if len(data) != spec['bytes'] or sha(data) != spec['sha256']:
            raise ValueError('unreviewed filter asset: ' + name)
        files[name] = data
    if output.exists():
        raise ValueError('refusing to overwrite prepared filter assets')
    (output / 'assets').mkdir(parents=True)
    for name, data in files.items():
        (output / 'assets' / name).write_bytes(data)
    (output / 'libAlgoInterface.rom-input.so').write_bytes(routed)
    report = dict(schema=1, installable=False, asset_root=contract['asset_root'],
                  assets=specs, native_sha256=sha(routed), scope=contract['scope'],
                  limits=contract['limits'])
    (output / 'provenance.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('native', 'assets', 'output'):
        parser.add_argument('--' + key, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.native, args.assets, args.output), indent=2))
