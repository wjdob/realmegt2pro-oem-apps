"""Inventory optional-app native inputs and check direct dependency exposure.

This reads archives, ELF metadata and target-files. It never loads target code,
modifies a package, grants access or claims that a projected namespace is live.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

from elf_symbols import dynamic_symbols


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_file(path, expected):
    if not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('full SHA-256 required')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            raise ValueError('archive identity differs')


def safe_path(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or str(path) != value
            or any(p in ('.', '..') for p in path.parts) or '\\' in value):
        raise ValueError('unsafe archive path')
    return value


def manifest(data):
    result = {}
    for line in data.decode('utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            raise ValueError('malformed payload manifest')
        digest, name = match.groups()
        safe_path(name)
        if name in result:
            raise ValueError('duplicate payload manifest path')
        result[name] = digest
    return result


def inspect_elf(data, readelf, scratch):
    if (len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01'
            or struct.unpack_from('<HH', data, 16) != (3, 183)):
        raise ValueError('expected little-endian ARM64 shared ELF')
    with tempfile.TemporaryDirectory(prefix='app-elf-', dir=scratch) as directory:
        path = Path(directory) / 'input.so'
        path.write_bytes(data)
        output = subprocess.run([str(readelf), '--dynamic', '--dyn-syms', '--wide', str(path)],
                                check=True, capture_output=True, text=True, timeout=60).stdout
    imports, weak, exports, unversioned = set(), set(), set(), set()
    for row in dynamic_symbols(output):
        name = row['name']
        if row['section'] == 'UND':
            if row['binding'] == 'GLOBAL':
                imports.add(name)
            elif row['binding'] == 'WEAK':
                weak.add(name)
        elif (row['binding'] in ('GLOBAL', 'WEAK', 'UNIQUE')
              and row['visibility'] in ('DEFAULT', 'PROTECTED')):
            exports.add(name.replace('@@', '@'))
            if '@' not in name or '@@' in name:
                exports.add(name.split('@')[0])
            if '@' not in name:
                unversioned.add(name)
    return dict(needed=re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', output),
                soname=re.findall(r'\(SONAME\).*?\[([^\]]+)\]', output),
                runpath=re.findall(r'\((?:RUNPATH|RPATH)\).*?\[([^\]]+)\]', output),
                imports=sorted(imports), weak_imports=sorted(weak),
                exports=sorted(exports), global_exports=sorted(unversioned))


def inventory(archive, expected, kind, profile, readelf, scratch):
    checked_file(archive, expected)
    if kind not in ('addon', 'apk') or not re.fullmatch('[a-z][a-z0-9-]*', profile):
        raise ValueError('invalid inventory kind/profile')
    libraries, total = [], 0
    with zipfile.ZipFile(archive) as package:
        if len(package.namelist()) != len(set(package.namelist())):
            raise ValueError('duplicate archive members')
        bindings = manifest(package.read('manifest.sha256')) if kind == 'addon' else {}
        for entry in sorted(package.infolist(), key=lambda x: x.filename):
            name = entry.filename
            if kind == 'addon':
                if not name.startswith('payload/') or not name.endswith('.so'):
                    continue
                path = safe_path(name[len('payload/'):])
                if re.fullmatch(r'(?:system|product)/(?:priv-app|app)/[^/]+/lib/arm64/[^/]+\.so', path):
                    scope = 'app'
                elif re.fullmatch(r'system/lib64/[^/]+\.so', path):
                    scope = 'platform'
                else:
                    raise ValueError('unreviewed native payload placement')
            else:
                if not name.startswith('lib/arm64-v8a/') or not name.endswith('.so'):
                    continue
                path, scope = safe_path(name), 'app'
                if len(PurePosixPath(path).parts) != 3:
                    raise ValueError('nested APK native member')
            total += entry.file_size
            if entry.file_size > 128 * 1024**2 or total > 768 * 1024**2:
                raise ValueError('native inventory exceeds reviewed size bound')
            data = package.read(entry)
            if kind == 'addon' and sha(data) != bindings.get(path):
                raise ValueError('native payload differs from manifest')
            row = dict(profile=profile, path=path, scope=scope,
                       sha256=sha(data), bytes=len(data))
            row.update(inspect_elf(data, readelf, scratch))
            libraries.append(row)
    return dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
                tool_sha256=sha(Path(__file__).read_bytes()), interpreter=sys.version,
                artifact_sha256=expected, kind=kind, profile=profile, libraries=libraries,
                signature_verified=False, runtime_qualified=False,
                limits='Native metadata and exact artifact/manifest binding only. APK/ZIP signature '
                       'verification, classloader/namespace state, dynamic dlopen/JNI, C++ ABI, '
                       'relocations, installation and runtime behavior are separate gates.')


def library_list(data):
    result = set()
    for line in data.decode('utf-8').splitlines():
        fields = line.split('#', 1)[0].split()
        if not fields:
            continue
        if (not re.fullmatch(r'[A-Za-z0-9_.+-]+\.so', fields[0])
                or any(value not in ('32', '64', 'nopreload') for value in fields[1:])
                or ('32' in fields and '64' in fields)):
            raise ValueError('unreviewed public/LLNDK list syntax')
        if '32' not in fields:
            result.add(fields[0])
    return result


def dependency_exposure(rows, public, llndk, domain, system_available):
    if domain not in ('system-bundled', 'product', 'data'):
        raise ValueError('explicit projected app domain required')
    app = [r for r in rows if r['scope'] == 'app']
    local = {}
    for row in app:
        name = PurePosixPath(row['path']).name
        if name in local:
            raise ValueError('ambiguous app-local native basename')
        local[name] = row['sha256']
    edges = []
    for row in app:
        for needed in row['needed']:
            if needed in local:
                reason = 'app-local'
            elif needed in public:
                reason = 'NDK-public'
            elif domain == 'product' and needed in llndk:
                reason = 'product-LLNDK'
            elif domain == 'system-bundled' and needed in system_available:
                reason = 'bundled-system-search'
            else:
                reason = 'unclassified'
            edges.append(dict(consumer=row['path'], needed=needed, exposure=reason))
    return dict(domain_projection=domain, edges=edges,
                unclassified=[r for r in edges if r['exposure'] == 'unclassified'],
                excluded_platform_roots=[r['path'] for r in rows if r['scope'] == 'platform'])


def visibility(source, target_files, expected, domain):
    checked_file(target_files, expected)
    lists = {}
    with zipfile.ZipFile(target_files) as package:
        for member in ('SYSTEM/etc/public.libraries.txt', 'SYSTEM/etc/llndk.libraries.txt'):
            data = package.read(member)
            lists[member] = dict(sha256=sha(data), libraries=sorted(library_list(data)))
        sdk = {line.split('=', 1)[1] for line in package.read('SYSTEM/build.prop').decode().splitlines()
               if line.startswith('ro.build.version.sdk=')}
        if sdk != {'37'}:
            raise ValueError('this placement model is reviewed only for API37')
        available = {PurePosixPath(n).name for n in package.namelist()
                     if re.fullmatch(r'(?:SYSTEM|SYSTEM_EXT)/lib64/[^/]+\.so', n)}
    result = dependency_exposure(source['libraries'],
        set(lists['SYSTEM/etc/public.libraries.txt']['libraries']),
        set(lists['SYSTEM/etc/llndk.libraries.txt']['libraries']), domain, available)
    return dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
                tool_sha256=sha(Path(__file__).read_bytes()), interpreter=sys.version,
                target_files_sha256=expected, source_artifact_sha256=source['artifact_sha256'],
                lists=lists, **result, runtime_qualified=False,
                limits='Direct app-JNI dependency exposure projection for the stated placement. '
                       'Product LLNDK access is distinct from public NDK access. Actual package '
                       'flags, updated/data APKs, shared SDK classloaders, platform helper '
                       'namespaces, transitive symbols, dynamic dlopen and runtime behavior '
                       'remain separate. An empty unclassified list is not an app pass.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inv = commands.add_parser('inventory')
    inv.add_argument('--archive', type=Path, required=True)
    inv.add_argument('--expected-sha256', required=True)
    inv.add_argument('--kind', choices=['addon', 'apk'], required=True)
    inv.add_argument('--profile', required=True)
    inv.add_argument('--readelf', type=Path, default=Path('/usr/bin/readelf'))
    inv.add_argument('--scratch', type=Path, required=True)
    vis = commands.add_parser('visibility')
    vis.add_argument('--inventory', type=Path, required=True)
    vis.add_argument('--target-files', type=Path, required=True)
    vis.add_argument('--expected-sha256', required=True)
    vis.add_argument('--domain', choices=['system-bundled', 'product', 'data'], required=True)
    for command in (inv, vis):
        command.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise ValueError('refusing to overwrite audit evidence')
    if options.command == 'inventory':
        options.scratch.mkdir(parents=True, exist_ok=True)
        result = inventory(options.archive, options.expected_sha256, options.kind,
                           options.profile, options.readelf, options.scratch)
    else:
        result = visibility(json.loads(options.inventory.read_text()), options.target_files,
                            options.expected_sha256, options.domain)
        result['inventory_sha256'] = sha(options.inventory.read_bytes())
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    summary = dict(output=str(options.output), runtime_qualified=False)
    if options.command == 'inventory':
        summary['libraries'] = len(result['libraries'])
    else:
        summary['unclassified'] = len(result['unclassified'])
    print(json.dumps(summary))
