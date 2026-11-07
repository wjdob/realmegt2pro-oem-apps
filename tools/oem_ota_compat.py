"""Check selected OEM add-on payloads against a destination target-files ZIP.

This is a host-side prerequisite for standard addon.d retention, not a hook,
installer, policy compiler or proof of app-data retention. The CLI verifies the
source ZIP signatures against the existing project recovery certificate.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

import addon_update_contract
import verify_signapk_ota


PACKAGES = {
    'camera': ('com.oplus.camera', 'b0a9bbfc05eee5e7d0a2c97c030586e15bb3301152078f54473bb82df6d8c818'),
    'photos': ('com.coloros.gallery3d', 'bd77b13eed39c33c246bd3340a2049dc35487fec6855191b74ac606fba730ada'),
}
PARTITIONS = {'system', 'system_ext', 'product', 'vendor', 'odm', 'vendor_dlkm'}
POLICY = frozenset('product/etc/selinux/' + name for name in (
    'product_mac_permissions.xml', 'product_seapp_contexts',
    'product_sepolicy.cil', 'product_sepolicy_and_mapping.sha256'))
STORAGE = 'product/etc/realme-oem-addons/camera-policy/'
COMPILER_INPUTS = frozenset((
    'system/etc/selinux/plat_sepolicy.cil',
    'system/etc/selinux/mapping/202504.cil',
    'system_ext/etc/selinux/system_ext_sepolicy.cil',
    'system_ext/etc/selinux/mapping/202504.cil',
    'product/etc/selinux/product_sepolicy.cil',
    'product/etc/selinux/mapping/202504.cil',
    'vendor/etc/selinux/plat_pub_versioned.cil',
    'vendor/etc/selinux/vendor_sepolicy.cil',
    'odm/etc/selinux/odm_sepolicy.cil',
    'system/etc/selinux/plat_sepolicy_genfs_202504.cil',
))
HEX = re.compile('[0-9a-f]{64}')


def safe_path(path):
    parts = PurePosixPath(path).parts
    if (not path or path.startswith('/') or '\\' in path
            or any(c.isspace() or ord(c) < 32 for c in path)
            or any(p in ('', '.', '..') for p in path.split('/'))
            or not parts or parts[0] not in PARTITIONS):
        raise ValueError('unsafe payload path: ' + path)
    return path


def index(archive):
    infos = archive.infolist()
    names = [i.filename for i in infos]
    if len(names) != len(set(names)):
        raise ValueError('duplicate ZIP member')
    for item in infos:
        path = item.filename.rstrip('/')
        if (not path or path.startswith('/') or '\\' in path
                or any(p in ('', '.', '..') for p in path.split('/'))
                or any(ord(c) < 32 for c in path)):
            raise ValueError('unsafe ZIP member: ' + item.filename)
    return {i.filename: i for i in infos if not i.is_dir()}


def digest_member(archive, member):
    info = archive.getinfo(member)
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError('inspected dependency is a symlink: ' + member)
    if info.file_size > 512 * 1024**2:
        raise ValueError('member exceeds inspection bound: ' + member)
    with archive.open(info) as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def small(archive, member):
    info = archive.getinfo(member)
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError('inspected metadata is a symlink: ' + member)
    if info.file_size > 1024**2:
        raise ValueError('metadata exceeds inspection bound: ' + member)
    return archive.read(member)


def hashes(data):
    result = {}
    for line in data.decode().splitlines():
        if '  ' not in line:
            raise ValueError('invalid hash manifest row')
        digest, path = line.split('  ', 1)
        safe_path(path)
        if not HEX.fullmatch(digest) or path in result:
            raise ValueError('invalid or duplicate hash manifest row')
        result[path] = digest
    if not result:
        raise ValueError('empty hash manifest')
    return result


def bundle(archive, profile):
    if profile not in PACKAGES:
        raise ValueError('unsupported optional profile')
    members = index(archive)
    manifest = hashes(small(archive, 'manifest.sha256'))
    payload = {n[len('payload/'):] for n in members if n.startswith('payload/')}
    if payload != manifest.keys() or len(payload) > 2048:
        raise ValueError('payload and ownership manifest differ')
    if sum(members['payload/' + p].file_size for p in payload) > 2 * 1024**3:
        raise ValueError('payload exceeds inspection bound')
    for path, digest in manifest.items():
        if digest_member(archive, 'payload/' + path) != digest:
            raise ValueError('payload hash differs: ' + path)
    receipt_path = 'product/etc/realme-oem-addons/' + profile + '.json'
    receipt = json.loads(small(archive, 'payload/' + receipt_path))
    package, signer = PACKAGES[profile]
    if (receipt.get('id') != profile or receipt.get('package') != package
            or receipt.get('certificate_sha256') != signer):
        raise ValueError('unexpected package/signer receipt')
    apks = [p for p in manifest if p.endswith('.apk')]
    if len(apks) != 1 or manifest[apks[0]] != receipt.get('sha256'):
        raise ValueError('APK and package receipt differ')
    bindings = hashes(small(archive, 'policy/inputs.sha256'))
    if set(bindings) != COMPILER_INPUTS:
        raise ValueError('incomplete or unsupported compiler inputs')
    if digest_member(archive, 'policy/inputs.sha256') != manifest.get(STORAGE + 'inputs.sha256'):
        raise ValueError('policy inputs lack payload binding')
    rows = small(archive, 'policy/patches.tsv').decode().splitlines()
    if digest_member(archive, 'policy/patches.tsv') != manifest.get(STORAGE + 'patches.tsv'):
        raise ValueError('policy transaction lacks payload binding')
    patch_paths = set()
    for row in rows:
        fields = row.split()
        if len(fields) != 3:
            raise ValueError('invalid policy transaction')
        after, before, path = fields
        if (path not in POLICY or path in patch_paths
                or not HEX.fullmatch(before) or not HEX.fullmatch(after)
                or manifest.get(path) != after):
            raise ValueError('unexpected policy transaction')
        name = PurePosixPath(path).name
        if (digest_member(archive, 'policy/preimages/' + name) != before
                or digest_member(archive, 'policy/postimages/' + name) != after
                or manifest.get(STORAGE + 'preimages/' + name) != before
                or manifest.get(STORAGE + 'postimages/' + name) != after):
            raise ValueError('policy pre/post image differs')
        if path in bindings and bindings[path] != before:
            raise ValueError('conflicting policy baseline')
        bindings[path] = before
        patch_paths.add(path)
    if patch_paths != POLICY:
        raise ValueError('incomplete policy transaction')
    native = hashes(small(archive, 'native-requirements.sha256'))
    if native != receipt.get('native_requirements'):
        raise ValueError('native requirements and receipt differ')
    for path, digest in native.items():
        if path in bindings and bindings[path] != digest:
            raise ValueError('conflicting native baseline')
        bindings[path] = digest
    result = {'profile': profile, 'package': package, 'receipt_signer_sha256': signer,
            'apk_sha256': receipt['sha256'], 'manifest': manifest,
            'target_bindings': bindings}
    retention = receipt.get('ota_retention')
    if isinstance(retention, dict):
        import oem_retention
        if (set(retention) != {'kind', 'template_sha256'}
                or retention['kind'] != oem_retention.KIND
                or retention['template_sha256'] != hashlib.sha256(oem_retention.TEMPLATE.read_bytes()).hexdigest()
                or not HEX.fullmatch(receipt.get('platform_framework_sha256', ''))):
            raise ValueError('unreviewed bundle retention contract')
        bindings['system/framework/oplus-fwk.jar'] = receipt['platform_framework_sha256']
        expected_hook = oem_retention.render(result)
        hook = 'system/addon.d/' + ('80-realme-camera.sh' if profile == 'camera' else '81-realme-photos.sh')
        if manifest.get(hook) != hashlib.sha256(expected_hook).hexdigest():
            raise ValueError('bundle retention hook differs')
    return result


def inspect(target, selected, certificate):
    members = index(target)
    if len({s['profile'] for s in selected}) != len(selected):
        raise ValueError('duplicate selected profile')
    # The existing prerequisite checker opens its own ZipFile over a seekable
    # stream. It does not close this caller-owned stream on return.
    prerequisite = addon_update_contract.inspect(target.fp, certificate)
    props = small(target, 'SYSTEM/build.prop').decode()
    if 'ro.build.version.sdk=36' not in props.splitlines():
        raise ValueError('Android16 destination required')
    wanted = {}
    owned = {}
    owners = {}
    for item in selected:
        for path, digest in item['target_bindings'].items():
            if path in wanted and wanted[path] != digest:
                raise ValueError('conflicting selected target bindings: ' + path)
            wanted[path] = digest
        for path, digest in item['manifest'].items():
            if path in owned and owned[path] != digest:
                raise ValueError('conflicting shared payload: ' + path)
            owned[path] = digest
            owners.setdefault(path, []).append(item['profile'])
    rows = []
    for path, expected in sorted(wanted.items()):
        part, relative = path.split('/', 1)
        member = part.upper() + '/' + relative
        if member not in members:
            raise ValueError('missing target dependency: ' + path)
        actual = digest_member(target, member)
        if actual != expected:
            raise ValueError('target dependency differs: ' + path)
        rows.append({'path': path, 'sha256': actual})
    for path in owned:
        part, relative = path.split('/', 1)
        if part.upper() + '/' + relative in members and path not in POLICY:
            raise ValueError('additive payload collides with base: ' + path)
    return {'oem_target_prerequisites_pass': True,
            'profiles': [{k: s[k] for k in ('profile', 'package', 'apk_sha256', 'receipt_signer_sha256')} for s in selected],
            'target_bindings': rows, 'owned_files': len(owned),
            'shared_paths': {p: names for p, names in sorted(owners.items()) if len(names) > 1},
            'addon_update_prerequisites': prerequisite,
            'limits': 'Host destination/ownership prerequisites only. No generated retention hook, '
                      'policy compilation, APK signer re-verification, device state, capacity, '
                      'OTA application, data retention or successful removal claim.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-files', type=Path, required=True)
    parser.add_argument('--camera-install', type=Path)
    parser.add_argument('--photos-install', type=Path)
    parser.add_argument('--certificate', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    certificate = args.certificate.read_bytes()
    expected = addon_update_contract.certificate_hash(certificate)
    signatures = []
    selected = []
    for profile, path in [('camera', args.camera_install), ('photos', args.photos_install)]:
        if path is None:
            continue
        with zipfile.ZipFile(path) as archive:
            index(archive)
            signature = verify_signapk_ota.signer_cert(path, archive, args.scratch, 'openssl')
            if not signature['signer_matches_otacert'] or signature['cms_signer_sha256'] != expected:
                raise ValueError('bundle is not signed by the project recovery key')
            selected.append(bundle(archive, profile))
        signatures.append({'profile': profile, 'zip_sha256': verify_signapk_ota.sha(path), 'signature': signature})
    with zipfile.ZipFile(args.target_files) as target:
        report = inspect(target, selected, certificate)
    report['bundle_signatures'] = signatures
    report['target_files_sha256'] = verify_signapk_ota.sha(args.target_files)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'oem_target_prerequisites_pass': True, 'selected_profiles': len(selected),
                      'shared_paths': len(report['shared_paths'])}))


if __name__ == '__main__':
    main()
