"""Compile the optional installer helper against an exact collected recovery.

Uses platform CRT/headers and the recovery's own dynamic libraries. Does not
change the Android source tree, build outputs, mappings or any attached device.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/oem_addons/map_logical.cpp'
LIBRARIES = ('libfs_mgr.so', 'liblp.so', 'libbase.so', 'libc++.so', 'libc.so', 'libdl.so')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build(android, target_files, out, sdk=36):
    compilers = {36: 'clang-r563880c', 37: 'clang-r596125'}
    if sdk not in compilers:
        raise ValueError('reviewed API36/37 compiler profile required')
    with zipfile.ZipFile(target_files) as archive:
        props = archive.read('RECOVERY/RAMDISK/prop.default').decode().splitlines()
        values = {line.split('=',1)[1] for line in props if line.startswith('ro.build.version.sdk=')}
        if values != {str(sdk)}:
            raise ValueError('recovery SDK differs from compiler profile')
    out.mkdir(parents=True, exist_ok=False)
    lib = out / 'lib'
    lib.mkdir()
    with zipfile.ZipFile(target_files) as archive:
        for name in archive.namelist():
            if (name.startswith('RECOVERY/RAMDISK/system/lib64/')
                    and name.endswith('.so') and name.count('/') == 4):
                (lib / Path(name).name).write_bytes(archive.read(name))
    compiler = android / 'prebuilts/clang/host/linux-x86' / compilers[sdk]
    bionic = android / 'bionic/libc'
    includes = [bionic/'include', bionic, bionic/'kernel/uapi',
                bionic/'kernel/uapi/asm-arm64', bionic/'kernel/android/uapi',
                android/('system/fs/fs_mgr/liblp/include' if sdk==37 else 'system/core/fs_mgr/liblp/include'),
                android/'system/libbase/include']
    target = '--target=aarch64-linux-android'+str(sdk)
    flags = [target, '-nostdlibinc', '-D__ANDROID_RECOVERY__', '-fPIC', '-O2']
    for path in includes:
        flags += ['-isystem', str(path)]
    commands = []

    def run(args):
        commands.append(args)
        result = subprocess.run(args, capture_output=True, text=True, timeout=180)
        (out/('command-'+str(len(commands))+'.txt')).write_text(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:])

    def compile_one(driver, source, output, extra=()):
        run([str(compiler/'bin'/driver), *flags, *extra, '-MD', '-MF', str(out/(output+'.d')),
             '-c', str(source), '-o', str(out/output)])

    compile_one('clang', bionic/'arch-common/bionic/crtbegin.c', 'crtbegin.o', ['-D__LIBC__'])
    compile_one('clang++', SOURCE, 'map.o', ['-nostdinc++', '-isystem',
        str(compiler/'android_libc++/platform/aarch64/include/c++/v1'), '-isystem',
        str(android/'external/libcxx/include'), '-isystem', str(android/'external/libcxxabi/include'),
        '-std=c++20', '-fno-exceptions', '-fno-rtti'])
    compile_one('clang', bionic/'arch-common/bionic/crtend.S', 'crtend.o')
    binary = out/'realme-prepare-logical'
    run([str(compiler/'bin/clang++'), target, '-nostdlib',
         '-fuse-ld=lld', '-Wl,-e,_start', '-Wl,-dynamic-linker,/system/bin/linker64',
         '-Wl,-z,relro,-z,now', '-Wl,--build-id=sha1', '-pie', str(out/'crtbegin.o'),
         str(out/'map.o'), '-L'+str(lib), '-Wl,-rpath-link,'+str(lib), '-lfs_mgr', '-llp',
         '-lbase', '-l:libc++.so', '-lc', '-ldl', str(out/'crtend.o'), '-o', str(binary)])
    control_binary = None
    if sdk == 37:
        control = ROOT/'tools/device/recovery_control.c'
        compile_one('clang', control, 'control.o', ['-std=c11', '-Wall', '-Wextra', '-Werror'])
        control_binary = out/'realme-recovery-control'
        run([str(compiler/'bin/clang'), target, '-nostdlib', '-fuse-ld=lld',
             '-Wl,-e,_start', '-Wl,-dynamic-linker,/system/bin/linker64',
             '-Wl,-z,relro,-z,now', '-Wl,--build-id=sha1', '-pie', str(out/'crtbegin.o'),
             str(out/'control.o'), '-L'+str(lib), '-Wl,-rpath-link,'+str(lib),
             '-lc', '-ldl', str(out/'crtend.o'), '-o', str(control_binary)])
    dependencies = {SOURCE}
    for path in out.glob('*.d'):
        # Inputs are exact workspace/platform paths without whitespace.
        dependencies.update(Path(p) for p in path.read_text().replace('\\\n', ' ').split(':', 1)[1].split())
    report = dict(status='compiled; recovery execution unvalidated', binary_sha256=sha(binary),
                  sdk=sdk, control_sha256=sha(control_binary) if control_binary else None,
                  builder_sha256=sha(Path(__file__)),
                  target_files_sha256=sha(target_files), source_sha256=sha(SOURCE),
                  compiler_sha256=sha(compiler/'bin/clang'), commands=commands,
                  libraries={name: sha(lib/name) for name in LIBRARIES},
                  inputs={str(p): sha(p) for p in sorted(dependencies)})
    (out/'build-report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-tree', type=Path, required=True)
    parser.add_argument('--target-files', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--sdk', type=int, choices=(36,37), default=36)
    args = parser.parse_args()
    result = build(args.android_tree, args.target_files, args.out, args.sdk)
    print(json.dumps({k: result[k] for k in ('status', 'binary_sha256')}))
