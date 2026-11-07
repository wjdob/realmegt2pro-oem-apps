"""Prepare an optional, app-local stock filter provider and its bounded loader fix.

Original ARM64 instructions, filter entry points and APK signatures are retained.
Only the wrapper's vendor-namespace loader import and its ELF symbol version change.
No installer, device operation, platform library or linker-policy change is performed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'configs/oem-camera-filters-23.2.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def local_loader(original, expected_input, expected_output):
    if sha(original) != expected_input:
        raise ValueError('unreviewed filter loader input')
    data = bytearray(original)
    if data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
        raise ValueError('expected ARM64 ELF filter loader')
    offset = struct.unpack_from('<Q', data, 40)[0]
    size, count = struct.unpack_from('<HH', data, 58)
    if size != 64 or offset + size * count > len(data):
        raise ValueError('unexpected ELF section layout')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, offset + i * size)
                for i in range(count)]
    dynamic = next(s for s in sections if s[1] == 11)
    strings = sections[dynamic[6]]
    versions = next(s for s in sections
                    if s[1] == 0x6fffffff and s[6] == sections.index(dynamic))
    symbols = {}
    for index in range(dynamic[5] // dynamic[9]):
        name = struct.unpack_from('<I', data, dynamic[4] + index * dynamic[9])[0]
        pos = strings[4] + name
        symbols[data[pos:data.index(0, pos)].decode()] = index, pos
    index, pos = symbols['android_load_sphal_library']
    reference, _ = symbols['dlclose']
    version_pos = versions[4] + 2 * index
    old_version = struct.unpack_from('<H', data, version_pos)[0]
    new_version = struct.unpack_from('<H', data, versions[4] + 2 * reference)[0]
    if old_version != 3 or new_version != 4 or 'dlopen' in symbols:
        raise ValueError('unreviewed filter loader symbol/version layout')
    old_name = b'android_load_sphal_library'
    data[pos:pos + len(old_name)] = b'dlopen' + b'\0' * (len(old_name) - 6)
    struct.pack_into('<H', data, version_pos, new_version)
    changed = [i for i, (before, after) in enumerate(zip(original, data)) if before != after]
    if not all(pos <= i < pos + len(old_name) or version_pos <= i < version_pos + 2
               for i in changed):
        raise ValueError('filter mutation escaped import/version scope')
    # Independently preserve all allocated executable sections, not merely file length.
    for section in sections:
        if section[2] & 4:
            start, length = section[4:6]
            if data[start:start + length] != original[start:start + length]:
                raise ValueError('filter executable instructions changed')
    if len(data) != len(original) or sha(data) != expected_output:
        raise ValueError('canonical local filter loader differs')
    return bytes(data), changed


def prepare(loader, provider, output):
    contract = json.loads(CONTRACT.read_text())
    original, provider_data = loader.read_bytes(), provider.read_bytes()
    if (sha(provider_data) != contract['provider_sha256']
            or provider_data[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', provider_data, 18)[0] != 183):
        raise ValueError('unreviewed ARM64 filter provider')
    patched, changed = local_loader(original, contract['stock_loader_sha256'],
                                    contract['local_loader_sha256'])
    if output.exists():
        raise ValueError('refusing to overwrite a prepared filter profile')
    output.mkdir(parents=True)
    files = {'libMsEffectSdk.so': patched, 'libmsnativefilter.so': provider_data}
    for name, data in files.items():
        (output / name).write_bytes(data)
    report = dict(schema=1, files={n: dict(sha256=sha(b), bytes=len(b))
                                  for n, b in files.items()},
                  stock_loader_sha256=contract['stock_loader_sha256'],
                  changed_offsets=changed,
                  scope='App-local optional Camera loader/provider. Stock filter instructions '
                        'and provider are unchanged; import resolves through the owning app '
                        'namespace. No global provider or linker relaxation.',
                  limits='Preparation is not an installed app namespace, successful filter '
                         'initialization, rendering, asset/license validation or photo save.')
    (output / 'provenance.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('loader', 'provider', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.loader, args.provider, args.output), indent=2))
