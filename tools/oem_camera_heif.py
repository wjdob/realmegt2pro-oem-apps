"""Stage authentic private Camera HEIF dependencies, without building an installer.

The current /product app profile is incompatible with their private platform media
dependencies. Preserve original APK, firmware, platform libraries and linker policy.
Require a separately reviewed bundled-system placement before an installable release.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from oem_sdk import definitions, select_definitions, sha, write_jar

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT/'configs/oem-camera-heif-23.2.json'
WRITER = 'Lcom/oplus/media/OplusHeifWriter;'


def checked_inputs(base, framework, native):
    contract = json.loads(CONTRACT.read_text())
    if (sha(base.read_bytes()) != contract['base_sdk_sha256']
            or sha(framework.read_bytes()) != contract['stock_framework_sha256']):
        raise ValueError('unreviewed Camera SDK/framework input')
    for name, digest in contract['native_sha256'].items():
        data = (native/name).read_bytes()
        if (sha(data) != digest or data[:6] != b'\x7fELF\x02\x01'
                or int.from_bytes(data[18:20], 'little') != 183):
            raise ValueError('unreviewed AArch64 HEIF provider: ' + name)
    return contract


def build(base, framework, native, android, output, scratch):
    contract = checked_inputs(base, framework, native)
    if output.exists():
        raise ValueError('refusing to overwrite a staged HEIF profile')
    with zipfile.ZipFile(base) as z:
        if len(z.namelist()) != len(set(z.namelist())):
            raise ValueError('duplicate SDK ZIP members')
        members = {n: z.read(n) for n in z.namelist()}
    if set(members) != {'classes.dex', 'classes2.dex', 'classes3.dex',
                        'classes4.dex', 'classes5.dex', 'classes6.dex'}:
        raise ValueError('unexpected base SDK layout')
    if any(WRITER == name for data in members.values() for name, _ in definitions(data)):
        raise ValueError('HEIF class already defined by base SDK')
    with zipfile.ZipFile(framework) as z:
        candidates = [z.read(n) for n in z.namelist() if n.endswith('.dex')
                      and WRITER in {name for name, _ in definitions(z.read(n))}]
    if len(candidates) != 1:
        raise ValueError('unambiguous authentic HEIF writer required')
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='camera-heif-', dir=scratch) as directory:
        work = Path(directory)
        selected = work/'writer.dex'
        selected.write_bytes(select_definitions(candidates[0], {WRITER}))
        (work/'dex').mkdir()
        subprocess.run([
            str(android/'prebuilts/jdk/jdk21/linux-x86/bin/java'), '-Xmx256m',
            '-cp', str(android/'prebuilts/r8/r8.jar'), 'com.android.tools.r8.D8',
            '--lib', str(android/'prebuilts/sdk/36/public/android.jar'),
            '--min-api', '36', '--output', str(work/'dex'), str(selected)
        ], check=True, capture_output=True)
        writer = (work/'dex/classes.dex').read_bytes()
        if (sha(writer) != contract['authentic_writer_dex_sha256']
                or {name for name, _ in definitions(writer)} != {WRITER}):
            raise ValueError('canonical HEIF writer bytes/class scope changed')
    retained = {n: sha(data) for n, data in members.items()}
    members['classes7.dex'] = writer
    output.mkdir(parents=True)
    sdk = output/'camera-unit-sdk-heif.jar'
    write_jar(sdk, members)
    (output/'native').mkdir()
    for name, digest in contract['native_sha256'].items():
        shutil.copyfile(native/name, output/'native'/name)
        if sha((output/'native'/name).read_bytes()) != digest:
            raise ValueError('copied HEIF provider differs')
    report = dict(schema=1, installable=False, output_sdk_sha256=sha(sdk.read_bytes()),
                  output_sdk_bytes=sdk.stat().st_size,
                  base_sdk_sha256=contract['base_sdk_sha256'],
                  stock_framework_sha256=contract['stock_framework_sha256'],
                  retained_dex_sha256=retained,
                  authentic_writer_dex_sha256=sha(writer),
                  native_sha256=contract['native_sha256'],
                  placement_requirement=contract['placement_requirement'],
                  limits=contract['limits'])
    (output/'provenance.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-sdk', 'stock-framework', 'stock-native', 'android-tree',
                 'output', 'scratch'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = build(args.base_sdk, args.stock_framework, args.stock_native,
                   args.android_tree, args.output, args.scratch)
    print(json.dumps({k: result[k] for k in
                      ('installable', 'output_sdk_sha256', 'output_sdk_bytes')}))
