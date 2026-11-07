"""Stage the API37 alpha1 source inputs without signing, installing or changing APKs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import zipfile

import oem_addons as addons
import oem_platform as platform
import oem_runtime_policy as policy

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'configs/oem-addons-alpha1-24.0.json'
ACCEPTED = {
    'camera': ('oem-addons-camera15-gallery15-23.2.json',
               'a88fe35f2fb2431c41885bde941363bbd52e7ea3e7025f505fe5e2e5825949df'),
    'photos': ('oem-addons-gallery-alpha15-23.2.json',
               '1283780af6fd5d183a861a6ef5846094da60793e8a8bc7ce65b7c73aa9427a9b'),
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_reviewed(root, relative, data, expected):
    relative = addons.safe_path(relative)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('source bytes differ: ' + relative)
    path = root/relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('source destination escapes output')
    if path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError('conflicting shared source: ' + relative)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def read_member(archive, member):
    info = archive.getinfo(member)
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError('source payload member is a symbolic link')
    return archive.read(info)


def prepare(archives, policy_root, buffer, bridge, helper, output):
    if output.exists():
        raise ValueError('new source output directory required')
    config = json.loads(CONFIG.read_text())
    if addons.config_platform(config) != platform.ANDROID17:
        raise ValueError('API37 genesis config required')
    for name, (_, expected) in ACCEPTED.items():
        if sha(archives[name]) != expected:
            raise ValueError('accepted source archive differs: ' + name)
    camera = addons.checked_profile(config, 'camera')
    photos = addons.checked_profile(config, 'photos')
    if camera['policy'] != photos['policy']:
        raise ValueError('shared API37 policy differs')
    policy_spec = camera['policy']
    policy.load_bundle(policy_root, policy_spec['sha256'],
                       context=policy.context_for_profile(camera))
    overrides = {'libNativeWinBuffExchange.so': buffer, 'libHeifWinBufExchg-jni.so': bridge}
    for spec in camera['platform_native']:
        if Path(spec['path']).name in overrides:
            if sha(overrides[Path(spec['path']).name]) != spec['sha256']:
                raise ValueError('reviewed API37 helper differs')
    if sha(helper) != camera['recovery_helper']['sha256']:
        raise ValueError('reviewed API37 recovery mapper differs')
    output.mkdir(parents=True)
    for name, (old_config, _) in ACCEPTED.items():
        profile = addons.checked_profile(config, name)
        old = json.loads((ROOT/'configs'/old_config).read_text())
        old_app = addons.app_path(addons.checked_profile(old, name))
        with zipfile.ZipFile(archives[name]) as archive:
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError('duplicate source archive members')
            jobs = [(profile, old_app+'/base.apk'),
                    (config['shared_sdk'], config['shared_sdk']['jar_path'])]
            jobs += [(s, old_app+'/lib/arm64/'+s['name']) for s in profile.get('extra_native', ())]
            jobs += [(s, s['path']) for s in profile.get('extra_assets', ())]
            for spec, member in jobs:
                write_reviewed(output, spec['source'], read_member(archive, 'payload/'+member), spec['sha256'])
            for spec in profile.get('platform_native', ()):
                override = overrides.get(Path(spec['path']).name)
                data = override.read_bytes() if override else read_member(archive, 'payload/'+spec['path'])
                write_reviewed(output, spec['source'], data, spec['sha256'])
        write_reviewed(output, profile['recovery_helper']['source'], helper.read_bytes(),
                       profile['recovery_helper']['sha256'])
    # Copy only the validated reversible bundle and its public compilation proof.
    names = policy.support_paths() | {'manifest.json', 'mapping.cil', 'compile-report.json', 'compiled.policy'}
    for relative in names:
        data = policy.oem_policy.checked_file(policy_root, relative)
        write_reviewed(output, policy_spec['source']+'/'+relative, data, hashlib.sha256(data).hexdigest())
    policy.load_bundle(output/policy_spec['source'], policy_spec['sha256'],
                       context=policy.context_for_profile(camera))
    files = {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob('*')) if p.is_file()}
    report = {'created_utc': datetime.now(timezone.utc).isoformat(), 'config_sha256': sha(CONFIG),
              'tool_sha256': sha(Path(__file__)), 'source_archive_sha256': {n: sha(p) for n, p in archives.items()},
              'files': files, 'limits': 'Unchanged accepted APK/JNI/SDK inputs plus reviewed API37 components. '
              'No APK modification, signing, installation, classloader/ABI execution or runtime acceptance.'}
    (output/'source-report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['camera23-install', 'photos23-install', 'policy', 'buffer', 'bridge', 'mapper', 'out']:
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    result = prepare({'camera': args.camera23_install, 'photos': args.photos23_install},
                     args.policy, args.buffer, args.bridge, args.mapper, args.out)
    print(json.dumps({'staged_files': len(result['files']), 'config_sha256': result['config_sha256']}))
