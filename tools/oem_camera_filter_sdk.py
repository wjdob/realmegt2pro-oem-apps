"""Route the pinned optional Camera SDK's Meishe LUT initializer to owned assets.

The APK stays unchanged. A temporary DEX string edit is canonically rebuilt by D8;
the intermediate is never installed. Other SDK members and symbol references stay
unchanged. This prepares an add-on input and never operates on the device.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile
import zlib

from oem_sdk import definitions, sha, write_jar

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'configs/oem-camera-filter-sdk-23.2.json'


def strings(data):
    count, offset = struct.unpack_from('<II', data, 56)
    result = []
    for index in range(count):
        start = struct.unpack_from('<I', data, offset + 4 * index)[0]
        pos, value, shift = start, 0, 0
        while True:
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                break
            shift += 7
            if shift > 28:
                raise ValueError('invalid DEX string length')
        end = data.index(0, pos)
        result.append((start, pos, end, value, data[pos:end]))
    return result


def replace_path(data, contract):
    if (sha(data) != contract['input_dex_sha256'] or data[:4] != b'dex\n'
            or struct.unpack_from('<I', data, 32)[0] != len(data)
            or hashlib.sha1(data[32:]).digest() != data[12:32]
            or zlib.adler32(data[12:]) & 0xffffffff != struct.unpack_from('<I', data, 8)[0]):
        raise ValueError('unreviewed or invalid Camera SDK DEX')
    old, new = (contract[key].encode('ascii') for key in ('old_path', 'new_path'))
    matches = [row for row in strings(data) if row[4] == old]
    if len(matches) != 1 or data.count(old) != 1:
        raise ValueError('expected one exact SDK LUT string')
    start, pos, end, length, _ = matches[0]
    if (not new or b'\0' in new or len(new) >= 128 or len(new) > len(old)
            or pos != start + 1 or length != len(old)):
        raise ValueError('unreviewed LUT string representation')
    result = bytearray(data)
    result[start:end + 1] = bytes([len(new)]) + new + bytes(end - start - len(new))
    result[12:32] = hashlib.sha1(result[32:]).digest()
    struct.pack_into('<I', result, 8, zlib.adler32(result[12:]) & 0xffffffff)
    return bytes(result)  # Input to D8 only: D8 rebuilds sorted string IDs.


def references(data):
    values = [row[4].decode(errors='replace') for row in strings(data)]
    count, off = struct.unpack_from('<II', data, 64)
    types = [values[struct.unpack_from('<I', data, off + 4*i)[0]] for i in range(count)]
    count, off = struct.unpack_from('<II', data, 72)
    protos = []
    for i in range(count):
        _, ret, params = struct.unpack_from('<III', data, off + 12*i)
        args = [] if not params else [types[struct.unpack_from('<H', data, params+4+2*j)[0]]
            for j in range(struct.unpack_from('<I', data, params)[0])]
        protos.append('(' + ''.join(args) + ')' + types[ret])
    result = {}
    for key, header, table in [('fields', 80, types), ('methods', 88, protos)]:
        count, off = struct.unpack_from('<II', data, header)
        rows = []
        for i in range(count):
            owner, typ, name = struct.unpack_from('<HHI', data, off+8*i)
            rows.append((types[owner], values[name], table[typ]))
        result[key] = sorted(rows)
    result['classes'] = sorted(name for name, _ in definitions(data))
    return result


def prepare(base, android, output, scratch):
    contract = json.loads(CONTRACT.read_text())
    if sha(base.read_bytes()) != contract['base_sdk_sha256'] or output.exists():
        raise ValueError('unreviewed SDK or existing output')
    with zipfile.ZipFile(base) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    original = members['classes.dex']
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='filter-sdk-', dir=scratch) as directory:
        work = Path(directory)
        (work/'intermediate.dex').write_bytes(replace_path(original, contract))
        (work/'out').mkdir()
        java = android/'prebuilts/jdk/jdk21/linux-x86/bin/java'
        r8 = android/'prebuilts/r8/r8.jar'
        subprocess.run([str(java), '-Xmx512m', '-cp', str(r8), 'com.android.tools.r8.D8',
            '--lib', str(android/'prebuilts/sdk/36/public/android.jar'), '--min-api', '36',
            '--output', str(work/'out'), str(work/'intermediate.dex')],
            check=True, capture_output=True)
        dex = (work/'out/classes.dex').read_bytes()
        if sha(dex) != contract['output_dex_sha256'] or references(original) != references(dex):
            raise ValueError('canonical SDK route or symbol references differ')
        if contract['old_path'].encode() in dex or dex.count(contract['new_path'].encode()) != 1:
            raise ValueError('SDK still references the old LUT directory')
        unchanged = {n: sha(b) for n, b in members.items() if n != 'classes.dex'}
        members['classes.dex'] = dex
        output.parent.mkdir(parents=True, exist_ok=True)
        write_jar(output, members)
        if sha(output.read_bytes()) != contract['output_sdk_sha256']:
            raise ValueError('canonical optional SDK differs')
    report = dict(base_sdk_sha256=contract['base_sdk_sha256'], output_sha256=sha(output.read_bytes()),
        dex_sha256=sha(dex), unchanged_members=unchanged, references_unchanged=True,
        limits='Offline canonical recipe only; no installed filter rendering or Camera acceptance.')
    output.with_suffix('.provenance.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-sdk', 'android-tree', 'output', 'scratch'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.base_sdk, args.android_tree, args.output, args.scratch), indent=2))
