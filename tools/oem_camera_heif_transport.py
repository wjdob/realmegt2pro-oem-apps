"""Prepare the optional Camera's pinned HEIF event transport and JNI glue.

No installation or device operations. Select the SDK's existing pointer-event
protocol instead of feeding its packets to a legacy flat-field decoder. Keep
the original encoding/buffer helpers and their real inputs/return values.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'configs/oem-camera-heif-transport-23.2.json'
SOURCE = ROOT / 'tools/oem_camera_compat/native/HeifWinBufferBridge.cpp'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def select_event_transport(original, contract):
    if sha(original) != contract['adapter_input_sha256']:
        raise ValueError('unreviewed HEIF adapter input')
    if (original[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', original, 18)[0] != 183):
        raise ValueError('expected ARM64 HEIF adapter')
    # Map the instruction through its executable PT_LOAD; the VA is not a file offset.
    offset = struct.unpack_from('<Q', original, 32)[0]
    size, count = struct.unpack_from('<HH', original, 54)
    if size != 56 or offset + size * count > len(original):
        raise ValueError('unexpected HEIF adapter ELF layout')
    segments = [struct.unpack_from('<IIQQQQQQ', original, offset + i * size)
                for i in range(count)]
    address = int(contract['selector_va'], 16)
    matches = [s for s in segments if s[0] == 1 and s[1] & 1
               and s[3] <= address and address + 4 <= s[3] + s[5]]
    if len(matches) != 1:
        raise ValueError('HEIF selector is not in one executable segment')
    segment = matches[0]
    position = segment[2] + address - segment[3]
    before, after = (bytes.fromhex(contract[key])
                     for key in ('selector_before', 'selector_after'))
    if len(before) != 4 or len(after) != 4 or original[position:position + 4] != before:
        raise ValueError('HEIF selector preimage differs')
    result = original[:position] + after + original[position + 4:]
    if sha(result) != contract['adapter_output_sha256']:
        raise ValueError('canonical HEIF event adapter differs')
    return result, position


def prepare(adapter, native, compiler_bin, output, scratch):
    contract = json.loads(CONTRACT.read_text())
    original = adapter.read_bytes()
    patched, position = select_event_transport(original, contract)
    if sha(SOURCE.read_bytes()) != contract['bridge_source_sha256']:
        raise ValueError('unreviewed HEIF JNI source')
    for name, digest in contract['helper_sha256'].items():
        if sha((native / name).read_bytes()) != digest:
            raise ValueError('unreviewed HEIF helper: ' + name)
    for name, digest in contract['compiler_sha256'].items():
        if sha((compiler_bin / name).read_bytes()) != digest:
            raise ValueError('unreviewed HEIF toolchain: ' + name)
    if output.exists():
        raise ValueError('refusing to overwrite prepared HEIF transport')
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='camera-heif-transport-', dir=scratch) as work:
        bridge = Path(work) / 'libHeifWinBufExchg-jni.so'
        command = [str(compiler_bin / 'clang++'), '--target=aarch64-linux-android36',
                   '-fPIC', '-O2', '-mbranch-protection=standard', '-fvisibility=hidden',
                   '-fno-exceptions', '-fno-rtti', '-nostdlib', '-shared',
                   '-Wl,--no-undefined', '-Wl,-soname,libHeifWinBufExchg-jni.so',
                   str(SOURCE), str(native / 'libHeifEncoderWrapper.so'),
                   str(native / 'libNativeWinBuffExchange.so'), '-o', str(bridge)]
        subprocess.run(command, check=True, capture_output=True)
        glue = bridge.read_bytes()
        if sha(glue) != contract['bridge_output_sha256']:
            raise ValueError('canonical HEIF JNI output differs')
    files = {'libAPSClient-cmd-jni.so': patched, 'libHeifWinBufExchg-jni.so': glue}
    report = dict(schema=1, installable=False,
                  files={n: dict(sha256=sha(data), bytes=len(data))
                         for n, data in files.items()},
                  selector_va=contract['selector_va'], selector_file_offset=position,
                  selector_before=contract['selector_before'],
                  selector_after=contract['selector_after'],
                  source_sha256=contract['bridge_source_sha256'],
                  helper_sha256=contract['helper_sha256'],
                  compiler_sha256=contract['compiler_sha256'],
                  scope=contract['scope'], limits=contract['limits'])
    output.mkdir(parents=True)
    for name, data in files.items():
        (output / name).write_bytes(data)
    (output / 'provenance.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('adapter', 'native', 'compiler-bin', 'output', 'scratch'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.adapter, args.native, args.compiler_bin,
                             args.output, args.scratch), indent=2))
