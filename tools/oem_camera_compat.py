"""Build Camera-local display compatibility in its optional shared SDK.

The pinned stock APK remains byte-for-byte unchanged. Its shared-library loader
provides only the reviewed DisplayUtil ABI, using normal per-window Android APIs.
This does not replace an android.* class or change the ROM framework.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

from oem_sdk import SUPPORT_PROFILES, definitions, prepare, sha, write_jar

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/oem_camera_compat/f6/q0.java'
APK_SHA = '9193280507b65cd35b0dda690d86a9f041392ba832fe5b036900207e409f0850'
SOURCE_SHA = 'd0b4f50f703ce7ff2423405cdc0a628274e5a4cd667cb5181f0a330d13419bbb'
DEX_SHA = 'ed456d84127a267d25087e5fc44c28de640ea5c62300bd9b00dbf9270d45cbf9'


def build(stock_root, camera_apk, android_tree, output, scratch, support_profile='shoulder-v3'):
    if sha(camera_apk.read_bytes()) != APK_SHA or sha(SOURCE.read_text().encode()) != SOURCE_SHA:
        raise ValueError('unreviewed Camera/display-adapter input')
    if output.exists():
        raise ValueError('refusing to overwrite compatibility SDK')
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='camera-compat-', dir=scratch) as tmp:
        work = Path(tmp)
        base = work / 'base.jar'
        provenance = prepare(stock_root, android_tree, base, work, support_profile)
        java = android_tree / 'prebuilts/jdk/jdk21/linux-x86/bin'
        sdk = android_tree / 'prebuilts/sdk/36/public/android.jar'
        (work / 'classes').mkdir()
        (work / 'dex').mkdir()
        commands = [
            [str(java / 'javac'), '--release', '17', '-cp', str(sdk),
             '-d', str(work / 'classes'), str(SOURCE)],
            [str(java / 'jar'), 'cf', str(work / 'adapter.jar'), '-C', str(work / 'classes'), '.'],
            [str(java / 'java'), '-Xmx256m', '-cp', str(android_tree / 'prebuilts/r8/r8.jar'),
             'com.android.tools.r8.D8', '--lib', str(sdk), '--min-api', '36',
             '--output', str(work / 'dex'), str(work / 'adapter.jar')],
        ]
        for command in commands:
            subprocess.run(command, check=True, capture_output=True)
        dex = (work / 'dex/classes.dex').read_bytes()
        if sha(dex) != DEX_SHA or {n for n, _ in definitions(dex)} != {'Lf6/q0;'}:
            raise ValueError('canonical adapter DEX/class scope changed')
        with zipfile.ZipFile(base) as archive:
            members = {n: archive.read(n) for n in archive.namelist()}
        if set(members) != {'classes.dex', 'classes2.dex'}:
            raise ValueError('unexpected prepared SDK layout')
        members['classes3.dex'] = dex
        output.parent.mkdir(parents=True, exist_ok=True)
        write_jar(output, members)
        provenance.update(camera_apk_sha256=APK_SHA, adapter_source_sha256=SOURCE_SHA,
                          adapter_dex_sha256=DEX_SHA, adapter_classes=['Lf6/q0;'],
                          compatible_profiles=['camera'],
                          output_sha256=sha(output.read_bytes()), output_bytes=output.stat().st_size,
                          scope='Private optional Camera SDK: authentic support profile plus '
                                'Camera-local per-window display adapter; APK and ROM unchanged.')
        output.with_suffix('.provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
        return provenance


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('stock-root', 'camera-apk', 'android-tree', 'output', 'scratch'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--support-profile', choices=sorted(SUPPORT_PROFILES), default='shoulder-v3')
    args = parser.parse_args()
    print(json.dumps(build(args.stock_root, args.camera_apk, args.android_tree, args.output,
                           args.scratch, args.support_profile)))
