"""Build a Camera-local Android window helper; do not edit the APK or framework.

Compile stubs only preserve the pinned APK ABI. They must never enter the output.
The authentic callback/singleton types remain separate from the new window backend.
"""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile

from oem_sdk import definitions, select_definitions, sha, write_jar

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'configs/oem-camera-window-23.2.json'
SOURCE = ROOT / 'tools/oem_camera_compat/ek/v0.java'
STUBS = ROOT / 'tools/oem_camera_compat/compile_stubs'
APK_SHA = '9193280507b65cd35b0dda690d86a9f041392ba832fe5b036900207e409f0850'
BASE_SHA = '57dd08dc7ee1a9eaa8a25ac5c5bae40eba3663a5b832d67aa8f1a040341dac43'
ADAPTER_TYPES = {'Lek/v0;', 'Lek/v0$WindowWatcher;'}
# Keep the legacy OEM listener in the APK loader: its superclass is APK-private.
# The new backend uses WindowWatcher; only its public callback and holder move here.
STOCK_TYPES = {'Lek/v0$b;', 'Lek/v0$c;'}


def abi(data, selected):
    """Read public field/method descriptors from the exact hash-bound DEX inputs."""
    def array(header, size, decoder):
        count, offset = struct.unpack_from('<II', data, header)
        return [decoder(offset + size*i) for i in range(count)]
    def string(pos):
        pos = struct.unpack_from('<I', data, pos)[0]
        while data[pos] & 128:
            pos += 1
        return data[pos+1:data.index(b'\0', pos+1)].decode(errors='replace')
    strings = array(56, 4, string)
    types = array(64, 4, lambda p: strings[struct.unpack_from('<I', data, p)[0]])
    def proto(pos):
        _, result, params = struct.unpack_from('<III', data, pos)
        arguments = []
        if params:
            arguments = [types[struct.unpack_from('<H', data, params+4+2*i)[0]]
                         for i in range(struct.unpack_from('<I', data, params)[0])]
        return '(' + ''.join(arguments) + ')' + types[result]
    protos = array(72, 12, proto)
    fields = array(80, 8, lambda p: struct.unpack_from('<HHI', data, p))
    methods = array(88, 8, lambda p: struct.unpack_from('<HHI', data, p))
    def uleb(pos):
        result, shift = 0, 0
        while True:
            value = data[pos]; pos += 1
            result |= (value & 127) << shift
            if value < 128:
                return result, pos
            shift += 7
            if shift > 28:
                raise ValueError('invalid class-data integer')
    result = {}
    for name, raw in definitions(data):
        if name not in selected:
            continue
        _, access, superclass, _, _, _, pos, _ = struct.unpack('<8I', raw)
        counts = []
        if pos:
            for _ in range(4):
                value, pos = uleb(pos); counts.append(value)
        else:
            # DEX permits class_data_off=0 for a class with no members.
            counts = [0, 0, 0, 0]
        row = dict(superclass=types[superclass], access=access, fields={}, methods={})
        for group, count in enumerate(counts):
            index = 0
            for _ in range(count):
                delta, pos = uleb(pos); index += delta
                flags, pos = uleb(pos)
                if group < 2:
                    _, typ, member = fields[index]
                    signature = strings[member] + ':' + types[typ]
                    target = row['fields']
                else:
                    _, prot, member = methods[index]
                    signature = strings[member] + protos[prot]
                    target = row['methods']
                    _, pos = uleb(pos)  # code offset
                if flags & 1:
                    target[signature] = flags
        result[name] = row
    return result


def source_files():
    # Keep this recipe independent of compile-only stubs for other adapters.
    return [SOURCE, STUBS/'android/app/OplusActivityTaskManager.java',
            STUBS/'ek/v0$a.java', STUBS/'ek/v0$b.java', STUBS/'ek/v0$c.java',
            STUBS/'og/d.java']


def compile_parts(apk, android, work):
    work.mkdir(parents=True, exist_ok=True)
    classes, adapter_out, stock_out = (work / n for n in ('classes', 'adapter-dex', 'stock-dex'))
    for path in (classes, adapter_out, stock_out):
        path.mkdir()
    java = android / 'prebuilts/jdk/jdk21/linux-x86/bin'
    sdk = android / 'prebuilts/sdk/36/public/android.jar'
    subprocess.run([str(java/'javac'), '--release', '17', '-cp', str(sdk), '-d', str(classes),
                    *map(str, source_files())], check=True, capture_output=True)
    # Only these two implementation classes enter D8; all compile stubs are excluded.
    adapter_jar = work/'adapter.jar'
    with zipfile.ZipFile(adapter_jar, 'w') as z:
        for member in ('ek/v0.class', 'ek/v0$WindowWatcher.class'):
            z.writestr(member, (classes/member).read_bytes())
    d8 = [str(java/'java'), '-Xmx256m', '-cp', str(android/'prebuilts/r8/r8.jar'),
          'com.android.tools.r8.D8', '--lib', str(sdk), '--min-api', '36']
    subprocess.run(d8 + ['--output', str(adapter_out), str(adapter_jar)],
                   check=True, capture_output=True)
    selected, original, original_abi = [], set(), {}
    with zipfile.ZipFile(apk) as z:
        for member in z.namelist():
            if not member.endswith('.dex'):
                continue
            data = z.read(member)
            names = {n for n, _ in definitions(data)} & STOCK_TYPES
            original_abi.update(abi(data, {'Lek/v0;'}))
            if names:
                path = work/('stock-' + member)
                path.write_bytes(select_definitions(data, names))
                selected.append(str(path)); original |= names
    if original != STOCK_TYPES:
        raise ValueError('authentic Camera callback types missing')
    subprocess.run(d8 + ['--output', str(stock_out), *selected], check=True, capture_output=True)
    adapter, stock = ((path/'classes.dex').read_bytes() for path in (adapter_out, stock_out))
    if ({n for n, _ in definitions(adapter)} != ADAPTER_TYPES
            or {n for n, _ in definitions(stock)} != STOCK_TYPES):
        raise ValueError('compile stub or unexpected class entered the output')
    replacement = abi(adapter, {'Lek/v0;'})['Lek/v0;']
    expected = original_abi['Lek/v0;']
    for group in ('fields', 'methods'):
        for signature, access in expected[group].items():
            flags = replacement[group].get(signature)
            if flags is None or (flags & (1|8|16)) != (access & (1|8|16)):
                raise ValueError('public Camera helper ABI differs: ' + signature)
    return adapter, stock, dict(original=expected, replacement=replacement)


def build(base, apk, android, output, scratch):
    contract = json.loads(CONTRACT.read_text())
    if sha(apk.read_bytes()) != APK_SHA or sha(base.read_bytes()) != BASE_SHA:
        raise ValueError('unreviewed Camera/SDK input')
    actual_sources = {str(p.relative_to(ROOT)): sha(p.read_text().encode()) for p in source_files()}
    if actual_sources != contract['sources_sha256']:
        raise ValueError('window adapter source pins changed')
    if output.exists():
        raise ValueError('refusing to overwrite a prepared SDK')
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='camera-window-', dir=scratch) as directory:
        adapter, stock, reviewed_abi = compile_parts(apk, android, Path(directory))
        if sha(adapter) != contract['adapter_dex_sha256'] or sha(stock) != contract['authentic_types_dex_sha256']:
            raise ValueError('canonical window adapter bytes changed')
        with zipfile.ZipFile(base) as z:
            members = {n: z.read(n) for n in z.namelist()}
        if set(members) != {'classes.dex', 'classes2.dex', 'classes3.dex'}:
            raise ValueError('unexpected base SDK layout')
        current = {n for data in members.values() for n, _ in definitions(data)}
        if current & (ADAPTER_TYPES | STOCK_TYPES):
            raise ValueError('window helper conflicts with the SDK')
        members.update({'classes4.dex': adapter, 'classes5.dex': stock})
        output.parent.mkdir(parents=True, exist_ok=True)
        write_jar(output, members)
    report = dict(base_sdk_sha256=BASE_SHA, apk_sha256=APK_SHA, sources_sha256=actual_sources,
                  adapter_dex_sha256=sha(adapter), authentic_types_dex_sha256=sha(stock),
                  abi=reviewed_abi, classes=sorted(ADAPTER_TYPES | STOCK_TYPES),
                  output_sha256=sha(output.read_bytes()), output_bytes=output.stat().st_size,
                  scope='Optional Camera helper only; actual owned Activity multi-window/PiP state '
                        'and view callbacks. No APK, boot framework or OEM-service emulation. '
                        'Compile stubs excluded. APK-only callback signatures require the real '
                        'child-loader call probe; isolated SDK reflection is insufficient. '
                        'ColorOS cross-app floating-window tracking is not provided.')
    output.with_suffix('.provenance.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-sdk', 'camera-apk', 'android-tree', 'output', 'scratch'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = build(args.base_sdk, args.camera_apk, args.android_tree, args.output, args.scratch)
    print(json.dumps({key: result[key] for key in ('output_sha256', 'output_bytes', 'classes')}))
