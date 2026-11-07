"""Build pinned, optional Camera session metadata and authentic Binder dependencies.

The original APK and boot framework remain untouched. D8 rebuilds the SDK's main
DEX with exactly one replacement proxy and one new caller helper; the other four
input DEX files stay byte-identical. Four authentic stock Binder types are appended.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

from oem_camera_window import abi
from oem_sdk import FRAMEWORK_SHA, definitions, select_definitions, sha, write_jar

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT/'configs/oem-camera-session-23.2.json'
BASE_SHA = '80a714f36adb26087f3616ea19d3948fd7d4171f5ace8428c1404b7dc38e5d54'
PROXY = 'Lcom/oplus/ocs/camera/metadata/parameter/CaptureRequestBuilderProxy;'
HELPER = 'Lrealme/camera/compat/SessionCaller;'
BINDER_TYPES = {'Lcom/oplus/app/IOplusProtectConnection' + suffix + ';'
                for suffix in ('', '$Default', '$Stub', '$Stub$Proxy')}
SOURCES = [ROOT/'tools/oem_camera_compat'/path for path in (
    'com/oplus/ocs/camera/metadata/parameter/CaptureRequestBuilderProxy.java',
    'realme/camera/compat/SessionCaller.java',
    'compile_stubs/com/oplus/ocs/camera/metadata/parameter/ICaptureRequestBuilderInternal.java')]


def compile_parts(base, framework, android, work):
    if sha(base.read_bytes()) != BASE_SHA or sha(framework.read_bytes()) != FRAMEWORK_SHA:
        raise ValueError('unreviewed SDK/framework input')
    work.mkdir(parents=True, exist_ok=True)
    for path in ('classes', 'main', 'binder'):
        (work/path).mkdir()
    java = android/'prebuilts/jdk/jdk21/linux-x86/bin'
    sdk = android/'prebuilts/sdk/36/public/android.jar'
    subprocess.run([str(java/'javac'), '--release', '17', '-cp', str(sdk),
                    '-d', str(work/'classes'), *map(str, SOURCES)],
                   check=True, capture_output=True)
    adapter = work/'implementation.jar'
    with zipfile.ZipFile(adapter, 'w') as z:
        for name in (PROXY, HELPER):
            member = name[1:-1] + '.class'
            z.writestr(member, (work/'classes'/member).read_bytes())
    with zipfile.ZipFile(base) as z:
        original = z.read('classes.dex')
        all_input = {n for m in z.namelist() for n, _ in definitions(z.read(m))}
    if not {PROXY} <= all_input or all_input & (BINDER_TYPES | {HELPER}):
        raise ValueError('unexpected session/Binder definitions in base')
    names = {n for n, _ in definitions(original)}
    selected = work/'original-without-proxy.dex'
    selected.write_bytes(select_definitions(original, names - {PROXY}))
    d8 = [str(java/'java'), '-Xmx512m', '-cp', str(android/'prebuilts/r8/r8.jar'),
          'com.android.tools.r8.D8', '--lib', str(sdk), '--min-api', '36']
    subprocess.run(d8 + ['--output', str(work/'main'), str(selected), str(adapter)],
                   check=True, capture_output=True)
    main = (work/'main/classes.dex').read_bytes()
    if sorted(p.name for p in (work/'main').iterdir()) != ['classes.dex']:
        raise ValueError('unexpected rebuilt main DEX layout')
    if {n for n, _ in definitions(main)} != names | {HELPER}:
        raise ValueError('compile stub or unexpected class entered rebuilt main DEX')
    expected, actual = abi(original, {PROXY})[PROXY], abi(main, {PROXY})[PROXY]
    if expected != actual:
        raise ValueError('replacement Camera request proxy public ABI differs')
    inputs, found = [], set()
    with zipfile.ZipFile(framework) as z:
        for member in z.namelist():
            if not member.endswith('.dex'):
                continue
            data = z.read(member)
            chosen = {n for n, _ in definitions(data)} & BINDER_TYPES
            if chosen:
                path = work/('binder-' + member)
                path.write_bytes(select_definitions(data, chosen))
                inputs.append(str(path)); found |= chosen
    if found != BINDER_TYPES:
        raise ValueError('authentic Binder closure missing')
    subprocess.run(d8 + ['--output', str(work/'binder'), *inputs],
                   check=True, capture_output=True)
    binder = (work/'binder/classes.dex').read_bytes()
    if {n for n, _ in definitions(binder)} != BINDER_TYPES:
        raise ValueError('unexpected Binder closure')
    return main, binder, dict(proxy_public_abi=actual,
                             original_main_definitions=len(names),
                             output_main_definitions=len(names) + 1,
                             binder_classes=sorted(BINDER_TYPES))


def build(base, framework, android, output, scratch):
    contract = json.loads(CONTRACT.read_text())
    sources = {str(p.relative_to(ROOT)): sha(p.read_text().encode()) for p in SOURCES}
    if sources != contract['sources_sha256']:
        raise ValueError('session adapter source pins changed')
    if output.exists():
        raise ValueError('refusing to overwrite prepared SDK')
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='camera-session-', dir=scratch) as directory:
        main, binder, checked = compile_parts(base, framework, android, Path(directory))
        if (sha(main) != contract['main_dex_sha256']
                or sha(binder) != contract['binder_dex_sha256']):
            raise ValueError('canonical session/Binder DEX bytes changed')
        with zipfile.ZipFile(base) as z:
            members = {n: z.read(n) for n in z.namelist()}
        if set(members) != {'classes.dex', 'classes2.dex', 'classes3.dex',
                            'classes4.dex', 'classes5.dex'}:
            raise ValueError('unexpected base SDK members')
        retained = {n: sha(data) for n, data in members.items() if n != 'classes.dex'}
        members.update({'classes.dex': main, 'classes6.dex': binder})
        output.parent.mkdir(parents=True, exist_ok=True)
        write_jar(output, members)
    report = dict(base_sdk_sha256=BASE_SHA, stock_framework_sha256=FRAMEWORK_SHA,
                  sources_sha256=sources, main_dex_sha256=sha(main),
                  binder_dex_sha256=sha(binder), retained_dex_sha256=retained,
                  **checked, output_sha256=sha(output.read_bytes()),
                  output_bytes=output.stat().st_size,
                  scope='Optional Camera SDK only: actual owning package session tag and '
                        'four authentic stock Binder types. One proxy replaced; main DEX '
                        'recompiled, other four DEX files unchanged. APK/boot framework, '
                        'auth/consent/permissions, services and HAL unmodified. Runtime unproven.')
    output.with_suffix('.provenance.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-sdk', 'stock-framework', 'android-tree', 'output', 'scratch'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = build(args.base_sdk, args.stock_framework, args.android_tree,
                   args.output, args.scratch)
    print(json.dumps({k: result[k] for k in ('output_sha256', 'output_bytes')}))
