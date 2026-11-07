"""Prepare the private optional Camera SDK without replacing the Lineage framework.

Retain stock SDK DEX/resources; isolate the reviewed absent data and camera wrappers
from the pinned stock framework, canonically rewrite them with D8, and append them
as a separate DEX. Explicit support profiles add reviewed authentic shoulder-pressure
and utility/audio wrappers with their stock-wrapper type closure. No profile
installs a service or replaces the boot framework. Never publish proprietary
inputs/output through Git.
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

SDK_SHA = 'd7da994f59c256237064847c25925098ad8f71cce1ac471a53b8929e4d6ddf16'
FRAMEWORK_SHA = '02b521ee95042df4a7076763db904235f05c5d0c0bd8549d5c39884f1b788517'
SUPPORT_SHA = '1e9f5441d63be5358cea779602b0bc6b16b8cc426ce4777ef25aa50d6a3069d9'
SUPPORT_CLASSES = {'Lcom/oplus/osense/eventinfo/EventConfig$1;',
                   'Lcom/oplus/osense/eventinfo/EventConfig;',
                   'Lcom/oplus/osense/eventinfo/OsenseConfig$1;',
                   'Lcom/oplus/osense/eventinfo/OsenseConfig;',
                   'Lcom/oplus/osense/eventinfo/OsenseEventResult$1;',
                   'Lcom/oplus/osense/eventinfo/OsenseEventResult;',
                   'Lcom/oplus/wrapper/hardware/camera2/CaptureResult$Key;',
                   'Lcom/oplus/wrapper/hardware/camera2/CaptureResult;',
                   'Lcom/oplus/wrapper/hardware/camera2/impl/CameraMetadataNative;'}

SHOULDER_CLASSES = {
    'Lcom/oplus/shoulderpressure/IOplusShoulderPressureManager$Default;',
    'Lcom/oplus/shoulderpressure/IOplusShoulderPressureManager$Stub$Proxy;',
    'Lcom/oplus/shoulderpressure/IOplusShoulderPressureManager$Stub;',
    'Lcom/oplus/shoulderpressure/IOplusShoulderPressureManager;',
    'Lcom/oplus/shoulderpressure/OplusShoulderPressureManager;',
    'Lcom/oplus/wrapper/os/ServiceManager;',
    'Lcom/oplus/wrapper/os/SystemProperties;',
}
UTILITY_CLASSES = {
    'Lcom/oplus/util/OplusTypeCastingHelper;',
    'Lcom/oplus/wrapper/media/AudioDeviceAttributes;',
    'Lcom/oplus/wrapper/media/AudioManager$AudioPortUpdateListenerImpl;',
    'Lcom/oplus/wrapper/media/AudioManager$OnAudioPortUpdateListener;',
    'Lcom/oplus/wrapper/media/AudioManager;',
    'Lcom/oplus/wrapper/media/AudioPatch;',
    'Lcom/oplus/wrapper/media/AudioPort;',
    'Lcom/oplus/wrapper/media/AudioPortConfig;',
    'Lcom/oplus/wrapper/media/audiopolicy/AudioMix$Builder;',
    'Lcom/oplus/wrapper/media/audiopolicy/AudioMix-IA;',
    'Lcom/oplus/wrapper/media/audiopolicy/AudioMix;',
    'Lcom/oplus/wrapper/media/audiopolicy/AudioMixingRule$Builder;',
    'Lcom/oplus/wrapper/media/audiopolicy/AudioMixingRule-IA;',
    'Lcom/oplus/wrapper/media/audiopolicy/AudioMixingRule;',
    'Lcom/oplus/wrapper/media/audiopolicy/AudioPolicy$Builder;',
    'Lcom/oplus/wrapper/media/audiopolicy/AudioPolicy-IA;',
    'Lcom/oplus/wrapper/media/audiopolicy/AudioPolicy;',
    'Lcom/oplus/wrapper/os/Trace;',
    'Lcom/oplus/wrapper/os/UserHandle;',
}
SUPPORT_PROFILES = {
    'base-v2': (SUPPORT_CLASSES, SUPPORT_SHA),
    'shoulder-v3': (SUPPORT_CLASSES | SHOULDER_CLASSES,
                    '06b618072e4ced2a0d0cb6c9e1f170142fc90ded574769c0ee86e260ba3309b6'),
    'utility-v5': (SUPPORT_CLASSES | SHOULDER_CLASSES | UTILITY_CLASSES,
                   'c1ebb83590feb579ce55bc4efcc650837509c43c40cf0283f824e78f6a2f1f65'),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def definitions(data):
    if data[:4] != b'dex\n' or len(data) < 112:
        raise ValueError('expected standard DEX header')
    count, offset = struct.unpack_from('<II', data, 56)
    strings = []
    for i in range(count):
        pos = struct.unpack_from('<I', data, offset + 4*i)[0]
        while data[pos] & 128:
            pos += 1
        pos += 1
        strings.append(data[pos:data.index(b'\0', pos)].decode('utf-8', errors='replace'))
    count, offset = struct.unpack_from('<II', data, 64)
    types = [strings[struct.unpack_from('<I', data, offset+4*i)[0]] for i in range(count)]
    count, offset = struct.unpack_from('<II', data, 96)
    return [(types[struct.unpack_from('<I', data, offset+32*i)[0]],
             data[offset+32*i:offset+32*(i+1)]) for i in range(count)]


def select_definitions(data, names):
    selected = [item for name, item in definitions(data) if name in names]
    if len(selected) != len(names):
        raise ValueError('expected exact reviewed class set in one DEX')
    result = bytearray(data)
    offset = struct.unpack_from('<I', result, 100)[0]
    for i, item in enumerate(selected):
        result[offset+32*i:offset+32*(i+1)] = item
    struct.pack_into('<I', result, 96, len(selected))
    offset = struct.unpack_from('<I', result, 52)[0]
    found = False
    for i in range(struct.unpack_from('<I', result, offset)[0]):
        pos = offset+4+12*i
        if struct.unpack_from('<H', result, pos)[0] == 6:
            struct.pack_into('<I', result, pos+4, len(selected))
            found = True
    if not found:
        raise ValueError('DEX class map missing')
    result[12:32] = hashlib.sha1(result[32:]).digest()
    struct.pack_into('<I', result, 8, zlib.adler32(result[12:]) & 0xffffffff)
    return bytes(result)  # Intermediate only: D8 must rebuild and strip orphaned data.


def write_jar(path, members):
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, (2009, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, data)


def prepare(stock_root, android_tree, output, scratch, support_profile='base-v2'):
    support_classes, support_sha = SUPPORT_PROFILES[support_profile]
    sdk = stock_root / 'com.oplus.camera.unit.sdk.jar'
    framework = stock_root / 'oplus-framework.jar'
    if sha(sdk.read_bytes()) != SDK_SHA or sha(framework.read_bytes()) != FRAMEWORK_SHA:
        raise ValueError('unreviewed stock SDK/framework input')
    if output.exists():
        raise ValueError('refusing to overwrite a prepared SDK')
    with zipfile.ZipFile(framework) as z:
        candidates = [z.read(n) for n in z.namelist() if n.endswith('.dex')
                      and support_classes <= {name for name, _ in definitions(z.read(n))}]
    if len(candidates) != 1:
        raise ValueError('unambiguous stock DTO definitions required')
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sdk-dto-', dir=scratch) as tmp:
        root = Path(tmp)
        (root/'selected.dex').write_bytes(select_definitions(candidates[0], support_classes))
        (root/'out').mkdir()
        r8 = android_tree/'prebuilts/r8/r8.jar'
        command = [str(android_tree/'prebuilts/jdk/jdk21/linux-x86/bin/java'), '-Xmx256m',
                   '-cp', str(r8), 'com.android.tools.r8.D8', '--lib',
                   str(android_tree/'prebuilts/sdk/36/public/android.jar'), '--min-api', '36',
                   '--output', str(root/'out'), str(root/'selected.dex')]
        subprocess.run(command, check=True, capture_output=True)
        dto = (root/'out/classes.dex').read_bytes()
        if sha(dto) != support_sha or {name for name, _ in definitions(dto)} != support_classes:
            raise ValueError('canonical DTO bytes/class scope changed; renew review')
        with zipfile.ZipFile(sdk) as z:
            if [n for n in z.namelist() if n.endswith('.dex')] != ['classes.dex']:
                raise ValueError('SDK DEX layout changed')
            members = {n: z.read(n) for n in z.namelist() if not n.endswith('/')
                       and not (n.upper().startswith('META-INF/') and
                                (n.upper().endswith(('.SF', '.RSA', '.DSA'))
                                 or n.upper() == 'META-INF/MANIFEST.MF'))}
        original_sdk_dex = sha(members['classes.dex'])
        members['classes2.dex'] = dto
        output.parent.mkdir(parents=True, exist_ok=True)
        write_jar(output, members)
        report = dict(stock_sdk_sha256=SDK_SHA, stock_framework_sha256=FRAMEWORK_SHA,
                      stock_sdk_dex_sha256=original_sdk_dex, dto_sha256=support_sha,
                      support_profile=support_profile,
                      dto_classes=sorted(support_classes), r8_sha256=sha(r8.read_bytes()),
                      output_sha256=sha(output.read_bytes()), output_bytes=output.stat().st_size,
                      scope='Private optional SDK only; original main SDK DEX/resources retained, '
                            'exact hash-pinned authentic support classes added; '
                            'no boot framework override or service installation.')
        output.with_suffix('.provenance.json').write_text(json.dumps(report, indent=2)+'\n')
        return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock-root', type=Path, required=True)
    p.add_argument('--android-tree', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--scratch', type=Path, required=True)
    p.add_argument('--support-profile', choices=sorted(SUPPORT_PROFILES), default='base-v2')
    a = p.parse_args()
    print(json.dumps(prepare(a.stock_root, a.android_tree, a.output, a.scratch,
                             a.support_profile)))
