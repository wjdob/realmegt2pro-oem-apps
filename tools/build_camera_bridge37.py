"""Compile the existing HEIF JNI packet bridge for the reviewed API37 helpers.

Produces a private component, with no installation or device access. The stock
encoder/writer and event ownership remain separate runtime qualification gates.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/oem_camera_compat/native/HeifWinBufferBridge.cpp'
PROFILE = ROOT / 'configs/oem-camera-bridge37-review.json'
BUFFER_PROFILE = ROOT / 'configs/oem-camera-native37-review.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare(native, compiler_bin, output):
    profile = json.loads(PROFILE.read_text())
    if profile.get('sdk') != 37 or profile.get('schema') != 1:
        raise ValueError('expected reviewed API37 bridge profile')
    if digest(BUFFER_PROFILE.read_bytes()) != profile['buffer_profile_sha256']:
        raise ValueError('buffer ABI cohort changed; review the bridge again')
    if digest(SOURCE.read_bytes()) != profile['source_sha256']:
        raise ValueError('unreviewed HEIF bridge source')
    if set(profile['helpers']) != {
            'libHeifEncoderWrapper.so', 'libNativeWinBuffExchange.so'}:
        raise ValueError('incomplete bridge helper binding')
    for name, expected in profile['helpers'].items():
        data = (native / name).read_bytes()
        if (digest(data) != expected or data[:6] != b'\x7fELF\x02\x01'
                or int.from_bytes(data[16:18], 'little') != 3
                or int.from_bytes(data[18:20], 'little') != 183):
            raise ValueError('unreviewed ARM64 helper: ' + name)
    if set(profile['compiler_sha256']) != {'clang++', 'clang++-real', 'ld.lld'}:
        raise ValueError('incomplete toolchain binding')
    for name, expected in profile['compiler_sha256'].items():
        if digest((compiler_bin / name).read_bytes()) != expected:
            raise ValueError('unreviewed HEIF compiler: ' + name)
    if output.exists():
        raise ValueError('refusing to overwrite bridge output')
    # All identities are checked before executing the compiler or creating output.
    output.mkdir(parents=True)
    binary = output / 'libHeifWinBufExchg-jni.so'
    command = [str(compiler_bin / 'clang++'), '--target=aarch64-linux-android37',
               '-fPIC', '-O2', '-mbranch-protection=standard', '-fvisibility=hidden',
               '-fno-exceptions', '-fno-rtti', '-nostdlib', '-shared',
               '-Wl,--no-undefined', '-Wl,--build-id=sha1', '-Wl,-z,relro,-z,now',
               '-Wl,-soname,libHeifWinBufExchg-jni.so', str(SOURCE),
               '-L' + str(native), '-l:libHeifEncoderWrapper.so',
               '-l:libNativeWinBuffExchange.so', '-o', str(binary)]
    process = subprocess.run(command, capture_output=True, text=True, timeout=120)
    (output / 'compile.log').write_text(process.stdout + process.stderr)
    if process.returncode:
        raise RuntimeError('bridge compile/link failed; see private compile.log')
    output_hash = digest(binary.read_bytes())
    if output_hash != profile['output_sha256']:
        raise ValueError('canonical API37 bridge output differs')
    report = dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
                  installable=False, runtime_qualified=False, sdk=37,
                  tool_sha256=digest(Path(__file__).read_bytes()),
                  profile_sha256=digest(PROFILE.read_bytes()),
                  source_sha256=profile['source_sha256'],
                  buffer_profile_sha256=profile['buffer_profile_sha256'],
                  helper_sha256=profile['helpers'],
                  compiler_sha256=profile['compiler_sha256'],
                  output_sha256=output_hash, command=command,
                  limits='Compile/link only; host packet checks are independent. '
                         'JNI loading, event pointer lifetime, encoder/writer ABI, '
                         'HEIC saving and installer/retention are unqualified.')
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('native', 'compiler-bin', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.native.resolve(), args.compiler_bin.resolve(),
                             args.output.resolve()), indent=2))
