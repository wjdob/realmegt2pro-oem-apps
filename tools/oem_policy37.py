"""Compile and stage reversible API37 optional-app policy from actual target-files.

Only four product policy files enter the add-on payload. Compiler inputs, stock
signers and the neverallow-enabled host command are independently hash-bound.
No device commands or installed kernel-policy changes are performed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

import oem_policy
import oem_policy_context as contexts
import oem_runtime_policy as runtime
import oem_platform

KIND = 'offline-api37-oem-policy-compile-v1'
PRODUCT_CIL = 'product/etc/selinux/product_sepolicy.cil'


def sha_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_compile_proof(report, config, config_sha, candidate):
    inputs = dict(config['inputs'])
    inputs[PRODUCT_CIL] = contexts.sha(candidate)
    inputs = {p: inputs[p] for p in config['compiler_inputs']}
    commands = report.get('commands', [])
    if (report.get('kind') != KIND or report.get('compile_passed') is not True
            or report.get('device_accessed') is not False
            or report.get('context_sha256') != config_sha
            or report.get('target_files_sha256') != config['target_files_sha256']
            or report.get('compiler_sha256') != config['compiler']['sha256']
            or report.get('platform') != oem_platform.ANDROID17
            or report.get('candidate_sha256') != contexts.sha(candidate)
            or report.get('input_hashes') != inputs
            or not re.fullmatch('[0-9a-f]{64}', report.get('compiled_policy_sha256', ''))
            or len(commands) != 1 or commands[0].get('code') != 0):
        raise ValueError('API37 policy compilation proof differs')
    args = commands[0]['args']
    if (len(args) != 11+len(config['compiler_inputs'])
            or args[1:7] != ['-m', '-M', 'true', '-G', '-c', '30']
            or args[-4] != '-o' or args[-2] != '-f'
            or any(arg == '-N' for arg in args)):
        raise ValueError('exact neverallow-enabled policy command required')
    for value, relative in zip(args[7:-4], config['compiler_inputs']):
        parts = PurePosixPath(relative).parts
        if PurePosixPath(value).parts[-len(parts):] != parts:
            raise ValueError('API37 policy compiler input order/path differs')


def prepare(target_files, compiler, camera_der, gallery_der, output, context):
    config = context.config
    if (sha_file(target_files) != config['target_files_sha256']
            or sha_file(compiler) != config['compiler']['sha256']):
        raise ValueError('policy source package/compiler differs from context')
    if output.exists():
        raise ValueError('refusing to overwrite policy bundle')
    # Validate identities and all preimages before creating output or compiling.
    runtime.certificate_map(camera_der, gallery_der)
    base = {}
    with zipfile.ZipFile(target_files) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('duplicate target-files members')
        props = archive.read('SYSTEM/build.prop').decode().splitlines()
        if ([s for s in props if s.startswith('ro.build.version.sdk=')] != ['ro.build.version.sdk=37']
                or 'ro.build.version.incremental='+config['build_incremental'] not in props
                or archive.read('VENDOR/etc/selinux/plat_sepolicy_vers.txt').decode().strip() != config['mapping_version']
                or archive.read('VENDOR/etc/selinux/genfs_labels_version.txt').decode().strip() != config['genfs_labels_version']):
            raise ValueError('API37 policy target identity differs')
        for relative, expected in config['inputs'].items():
            part, tail = relative.split('/', 1)
            data = archive.read(part.upper()+'/'+tail)
            if contexts.sha(data) != expected:
                raise ValueError('API37 policy input differs: '+relative)
            base[relative] = data
    prefix = 'product/etc/selinux/'
    mapping = base[contexts.PRODUCT_MAPPING]
    if (contexts.sha(base[PRODUCT_CIL]+mapping).encode()+b'\n' !=
            base[prefix+'product_sepolicy_and_mapping.sha256']):
        raise ValueError('base policy/mapping digest differs')
    mac = ET.fromstring(base[prefix+'product_mac_permissions.xml'])
    if mac.tag != 'policy' or len(mac):
        raise ValueError('API37 base product signer policy must be empty')
    pre = {p: base[p] for p in runtime.PATCH_PATHS}
    desired = runtime.desired_files(pre, camera_der, gallery_der, mapping, context=context)
    output.mkdir(parents=True)
    inputs = []
    input_hashes = {}
    for relative in config['compiler_inputs']:
        data = desired[PRODUCT_CIL] if relative == PRODUCT_CIL else base[relative]
        path = output/'compiler-inputs'/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        inputs.append(path)
        input_hashes[relative] = contexts.sha(data)
    compiled = output/'compiled.policy'
    command = [str(compiler), '-m', '-M', 'true', '-G', '-c', '30',
               *map(str, inputs), '-o', str(compiled), '-f', str(output/'compiled.file_contexts')]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    (output/'compiler.stdout').write_text(result.stdout)
    (output/'compiler.stderr').write_text(result.stderr)
    proof = dict(created_utc=datetime.now(timezone.utc).isoformat(), kind=KIND,
        compile_passed=result.returncode == 0 and compiled.is_file() and compiled.stat().st_size > 0,
        device_accessed=False, platform=dict(oem_platform.ANDROID17),
        context_sha256=context.expected_sha256, target_files_sha256=sha_file(target_files),
        compiler_sha256=sha_file(compiler), candidate_sha256=contexts.sha(desired[PRODUCT_CIL]),
        input_hashes=input_hashes, commands=[dict(args=command, code=result.returncode)],
        compiled_policy_sha256=sha_file(compiled) if compiled.is_file() else '',
        limits='Actual hash-bound host compilation with neverallow checks. No device, installed policy, app-domain behavior or kernel load test.')
    proof_raw = (json.dumps(proof, indent=2)+'\n').encode()
    (output/'compile-report.json').write_bytes(proof_raw)
    verify_compile_proof(proof, config, context.expected_sha256, desired[PRODUCT_CIL])
    patches = [dict(path=p, before=contexts.sha(pre[p]), after=contexts.sha(desired[p]),
                    preimage=runtime.STORAGE+'preimages/'+Path(p).name,
                    postimage=runtime.STORAGE+'postimages/'+Path(p).name)
               for p in sorted(runtime.PATCH_PATHS)]
    files = {}
    for row in patches:
        files[row['preimage']] = pre[row['path']]
        files[row['postimage']] = desired[row['path']]
    files[runtime.STORAGE+'inputs.sha256'] = ''.join(
        config['inputs'][p]+'  '+p+'\n' for p in config['runtime_inputs']).encode()
    files[runtime.STORAGE+'patches.tsv'] = ''.join(
        r['after']+' '+r['before']+' '+r['path']+'\n' for r in patches).encode()
    manifest = dict(schema=1, kind=runtime.KIND, platform=dict(oem_platform.ANDROID17),
        base_config_sha256=context.expected_sha256, target_files_sha256=config['target_files_sha256'],
        camera_fragment_sha256=config['camera_fragment_sha256'],
        gallery_fragment_sha256=config['gallery_fragment_sha256'],
        packages={runtime.CAMERA_PACKAGE: runtime.CAMERA_CERT_SHA,
                  runtime.GALLERY_PACKAGE: runtime.GALLERY_CERT_SHA},
        mapping_sha256=contexts.sha(mapping), patches=patches,
        files={p: contexts.sha(data) for p, data in sorted(files.items())},
        compile_report_sha256=contexts.sha(proof_raw),
        scope='Optional signer/package-bound Camera and Gallery domains. Four reversible product files; no permissive or global untrusted-app change.')
    for relative, data in files.items():
        path = output/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (output/'mapping.cil').write_bytes(mapping)
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    # Independent reconstruction refuses unintended mappings, hashes or cohort changes.
    runtime.load_bundle(output, sha_file(output/'manifest.json'), context=context)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('target-files', 'compiler', 'camera-cert-der', 'gallery-cert-der', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--context', required=True, help='reviewed configs/<name>.json')
    parser.add_argument('--context-sha256', required=True)
    args = parser.parse_args()
    context = contexts.load({'source': args.context, 'sha256': args.context_sha256})
    value = prepare(args.target_files, args.compiler, args.camera_cert_der.read_bytes(),
                    args.gallery_cert_der.read_bytes(), args.out, context)
    print(json.dumps({'platform': value['platform'], 'manifest_sha256': sha_file(args.out/'manifest.json'),
                      'scope': value['scope'], 'runtime_qualified': False}))


if __name__ == '__main__':
    main()
