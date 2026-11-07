"""Build the Camera buffer helper offline against the reviewed Native22 cohort.

The compile record comes from Ninja's read-only `-t commands -s` inspection of
libgui's Surface.o. A pinned normalized argument digest prevents accepting an
unrelated command. No platform source/cache writes, installation or device access.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'configs/oem-camera-native37-review.json'
SOURCE = ROOT / 'tools/oem_camera_compat/native/NativeWinBufferExchange37.cpp'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_provider(profile, member, data):
    if profile['provider_sha256'].get(member) != digest(data):
        raise ValueError('unreviewed native provider: ' + member)
    return data


def validate_profile(profile):
    libraries = {'libgui.so', 'libui.so', 'libutils.so', 'liblog.so',
                 'libc++.so', 'libc.so', 'libm.so', 'libdl.so', 'libnativewindow.so'}
    members = {'SYSTEM/lib64/' + name for name in libraries}
    members.add('SYSTEM/apex/com.android.runtime.apex')
    members.update('runtime/lib64/bionic/' + name for name in
                   ('libc.so', 'libm.so', 'libdl.so'))
    if (profile.get('schema') != 1 or profile.get('sdk') != 37
            or set(profile.get('link_libraries', [])) != libraries
            or len(profile['link_libraries']) != len(libraries)
            or set(profile.get('provider_sha256', {})) != members):
        raise ValueError('incomplete API37 native provider cohort')
    for value in [*profile['provider_sha256'].values(), profile.get('output_sha256')]:
        if (not isinstance(value, str) or len(value) != 64
                or any(c not in '0123456789abcdef' for c in value)):
            raise ValueError('invalid API37 native provider identity')


def header_inventory(tree, dependency_file):
    dependencies = dependency_file.read_text().replace('\\\n', ' ').split(':', 1)[1].split()
    result = {}
    for name in dependencies:
        path = tree / name
        if path.resolve() != SOURCE.resolve():
            result[name] = digest(path.read_bytes())
    return result


def normalized_compile(record):
    if record.get('exit_code') != 0:
        raise ValueError('compile-command inspection did not pass')
    args = shlex.split(record['command'])
    compiler = 'prebuilts/clang/host/linux-x86/clang-r596125/bin/clang++'
    if (args[:2] != ['PWD=/proc/self/cwd', compiler]
            or args[-1] != 'frameworks/native/libs/gui/Surface.cpp'):
        raise ValueError('expected the reviewed libgui Surface compile command')
    result = []
    i = 1
    while i < len(args) - 1:
        value = args[i]
        if value in ('-MF', '-o'):
            i += 2
            continue
        if value in ('-c', '-MD', '-MMD',
                     '-DANDROID_UTILS_REF_BASE_DISABLE_IMPLICIT_CONSTRUCTION'):
            i += 1
            continue
        if value.startswith(('-fprofile-sample-use=', '-fprofile-use=',
                             '-fmodules-cache-path=', '-fsample-profile-use-profi')):
            i += 1
            continue
        result.append(value)
        i += 1
    return result


def prepare(tree, host_bin, target_files, compile_record, output):
    profile = json.loads(PROFILE.read_text())
    validate_profile(profile)
    record = json.loads(compile_record.read_text())
    args = normalized_compile(record)
    if digest(json.dumps(args, separators=(',', ':')).encode()) != profile['normalized_compile_sha256']:
        raise ValueError('unreviewed platform compile arguments')
    if digest(SOURCE.read_bytes()) != profile['source_sha256']:
        raise ValueError('unreviewed buffer helper source')
    if output.exists():
        raise ValueError('refusing to overwrite candidate output')
    # These are read-only checks before creating output or executing a compiler.
    for name, expected in profile['platform_headers'].items():
        if digest((tree / name).read_bytes()) != expected:
            raise ValueError('unreviewed platform header: ' + name)
    compiler_bin = tree / Path(args[0]).parent
    for name, expected in profile['compiler_sha256'].items():
        if digest((compiler_bin / name).read_bytes()) != expected:
            raise ValueError('unreviewed compiler: ' + name)
    with target_files.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != profile['target_files_sha256']:
            raise ValueError('unreviewed target-files cohort')
    output.mkdir(parents=True)
    providers = output / 'providers'
    providers.mkdir()
    inputs = {}
    libraries = profile['link_libraries']
    with zipfile.ZipFile(target_files) as archive:
        for name in libraries:
            member = 'SYSTEM/lib64/' + name
            data = checked_provider(profile, member, archive.read(member))
            inputs[member] = digest(data)
            if data.startswith(b'\x7fELF'):
                (providers / name).write_bytes(data)
            elif data != ('/apex/com.android.runtime/lib64/bionic/' + name).encode():
                raise ValueError('unexpected platform library link: ' + name)
        apex = checked_provider(profile, 'SYSTEM/apex/com.android.runtime.apex',
                                archive.read('SYSTEM/apex/com.android.runtime.apex'))
        inputs['SYSTEM/apex/com.android.runtime.apex'] = digest(apex)
        (output / 'runtime.apex').write_bytes(apex)
    commands = []

    def run(command):
        commands.append(command)
        process = subprocess.run(command, cwd=tree, capture_output=True, text=True, timeout=180)
        (output / ('command-%d.log' % len(commands))).write_text(process.stdout + process.stderr)
        if process.returncode:
            raise RuntimeError('offline helper command failed; see private command log')

    run([str(host_bin / 'deapexer'), '--debugfs_path', str(host_bin / 'debugfs'),
         '--fsckerofs_path', str(host_bin / 'fsck.erofs'), 'extract',
         str(output / 'runtime.apex'), str(output / 'runtime')])
    for name in libraries:
        if not (providers / name).exists():
            member = 'runtime/lib64/bionic/' + name
            data = checked_provider(profile, member, (output / member).read_bytes())
            if not data.startswith(b'\x7fELF'):
                raise ValueError('runtime provider is not ELF: ' + name)
            inputs[member] = digest(data)
            (providers / name).write_bytes(data)
    builtins = list((compiler_bin.parent / 'lib/clang').glob(
        '*/lib/linux/libclang_rt.builtins-aarch64-android.a'))
    if len(builtins) != 1 or digest(builtins[0].read_bytes()) != profile['builtins_sha256']:
        raise ValueError('unreviewed compiler builtins')
    obj = output / 'buffer.o'
    run(args + ['-g0', '-fvisibility=hidden', '-c', str(SOURCE), '-MMD',
                '-MF', str(output / 'buffer.d'), '-o', str(obj)])
    headers = header_inventory(tree, output / 'buffer.d')
    inventory_sha256 = digest(json.dumps(headers, sort_keys=True, separators=(',', ':')).encode())
    if inventory_sha256 != profile['header_inventory_sha256']:
        raise ValueError('transitive platform/header inventory differs; review before linking')
    binary = output / 'libNativeWinBuffExchange.so'
    run([str(compiler_bin / 'clang++'), '--target=aarch64-linux-android37',
         '-nostdlib', '-shared', '-Wl,--no-undefined', '-Wl,--build-id=sha1',
         '-Wl,-z,relro,-z,now', '-Wl,-soname,libNativeWinBuffExchange.so',
         str(obj), *[str(providers / name) for name in libraries], str(builtins[0]),
         '-o', str(binary)])
    if digest(binary.read_bytes()) != profile['output_sha256']:
        raise ValueError('canonical API37 buffer helper output differs')
    report = dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
                  installable=False, runtime_qualified=False,
                  profile_sha256=digest(PROFILE.read_bytes()),
                  compile_record_sha256=digest(compile_record.read_bytes()),
                  compiler_sha256=profile['compiler_sha256'],
                  target_files_sha256=profile['target_files_sha256'],
                  source_sha256=digest(SOURCE.read_bytes()), inputs=inputs,
                  header_sha256=headers, header_inventory_sha256=inventory_sha256,
                  output_sha256=digest(binary.read_bytes()), commands=commands,
                  limits='Compile/link only. Namespace loading, event ownership, HEIC saving, '
                         'installer/co-install/removal and OTA retention remain unqualified.')
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['tree', 'host-bin', 'target-files', 'compile-record', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    options = parser.parse_args()
    result = prepare(options.tree.resolve(), options.host_bin.resolve(),
                     options.target_files.resolve(), options.compile_record.resolve(),
                     options.output.resolve())
    print(json.dumps({k: result[k] for k in
                      ['installable', 'runtime_qualified', 'output_sha256', 'limits']}))
