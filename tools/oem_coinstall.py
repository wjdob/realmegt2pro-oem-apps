"""Validate exact peer app manifests and immutable shared OEM runtime ownership.

This module performs no device operations. Sharing covers the product Java SDK,
complete pinned LUT inventory and an optional complete identical policy transaction.
Legacy Camera-only policy, privileges, native providers and APKs remain separately
owned. Archive/profile validation binds the shared policy to its exact app identities.
"""
import re
from pathlib import PurePosixPath, Path
import json
import hashlib
from functools import lru_cache
import oem_runtime_policy

ROOT = Path(__file__).resolve().parents[1]
SDK_PATHS = frozenset({'product/framework/realme-camera-unit-sdk.jar',
                       'product/etc/permissions/realme-camera-unit-sdk.xml'})
LEGACY_SDK_PATHS = frozenset({'system/framework/realme-camera-unit-sdk.jar',
                             'system/etc/permissions/realme-camera-unit-sdk.xml'})
CAMERA_LIBRARIES = frozenset('system/lib64/'+name for name in (
    'libHeifEncoderWrapper.so', 'libNativeWinBuffExchange.so',
    'libHeifWinBufExchg-jni.so', 'liboplusheifwriter.so'))
CAMERA_POLICY = frozenset('product/etc/selinux/'+name for name in (
    'product_mac_permissions.xml', 'product_seapp_contexts', 'product_sepolicy.cil',
    'product_sepolicy_and_mapping.sha256'))
APP_ROOTS = {'camera': 'system/priv-app/RealmeStockCamera',
             'photos': 'product/app/RealmeStockPhotos'}
HOOKS = {'camera': 'system/addon.d/80-realme-camera.sh',
         'photos': 'system/addon.d/81-realme-photos.sh'}


def app_root(profile, files):
    """Bind a versioned Gallery directory to the APK bytes, not to a clock."""
    apks = [p for p in files if p.endswith('/base.apk')]
    if len(apks) != 1:
        raise ValueError('one peer APK required')
    root = apks[0].removesuffix('/base.apk')
    if root == APP_ROOTS[profile]:
        return root
    if (profile != 'photos' or root != APP_ROOTS['photos']+'-'+files[apks[0]]):
        raise ValueError('app directory is not bound to APK content')
    return root


def shareable_paths():
    return SDK_PATHS | lut_paths() | oem_runtime_policy.shared_paths()


@lru_cache(maxsize=1)
def lut_paths():
    contract = json.loads((ROOT/'configs/oem-camera-filter-assets-23.2.json').read_text())
    if contract['asset_root'] != 'system/etc/realme-lut' or len(contract['files']) != 71:
        raise ValueError('unreviewed shared LUT inventory')
    return frozenset(contract['asset_root']+'/'+name for name in contract['files'])


def safe(path):
    if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_./+-]+', path)
            or '..' in path or path.startswith('/') or str(PurePosixPath(path)) != path):
        raise ValueError('unsafe peer path')
    return path


def allowed(profile, path):
    safe(path)
    if profile not in APP_ROOTS:
        return False
    if path in SDK_PATHS or path in lut_paths():
        return True
    if (path.startswith(APP_ROOTS[profile]+'/') or path == HOOKS[profile]
            or path == 'product/etc/sysconfig/realme-'+profile+'.xml'
            or path == 'product/etc/realme-oem-addons/'+profile+'.json'):
        return True
    if profile == 'camera':
        return (path in CAMERA_LIBRARIES or path in CAMERA_POLICY
                or path == 'system/etc/permissions/privapp-permissions-realme-camera.xml'
                or path.startswith('product/etc/realme-oem-addons/camera-policy/'))
    if profile == 'photos':
        return (path in oem_runtime_policy.shared_paths() or
                re.fullmatch(r'product/app/RealmeStockPhotos-[0-9a-f]{64}/(?:base\.apk|lib/arm64/lib[A-Za-z0-9_.+-]+\.so)',path) is not None)
    return False


def validate(profile, own, peers):
    """Return peers with exact, equal shared subsets; reject legacy/mixed runtimes.

    Inputs are hashes of complete already-prepared payloads, not installed receipts
    or guessed filenames. Runtime recognition must still verify every peer file.
    """
    if profile not in APP_ROOTS or not isinstance(peers, dict) or len(peers) > 8:
        raise ValueError('invalid peer profile/count')
    if not isinstance(own, dict) or not 4 <= len(own) <= 512:
        raise ValueError('invalid owner manifest')
    for path, digest in own.items():
        if not allowed(profile, path) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('owner exceeds profile or has invalid digest')
    app_root(profile, own)
    other = 'photos' if profile == 'camera' else 'camera'
    result = {}
    required_shared = SDK_PATHS | lut_paths()
    for version, entry in peers.items():
        if (not re.fullmatch(other+r'-[a-z0-9][a-z0-9-]{0,31}', version)
                or not isinstance(entry, dict) or set(entry) != {'profile', 'files'}
                or entry['profile'] != other or not isinstance(entry['files'], dict)):
            raise ValueError('invalid peer version/profile')
        files = entry['files']
        if not 4 <= len(files) <= 512:
            raise ValueError('invalid peer file count')
        for path, digest in files.items():
            if not allowed(other, path) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('peer exceeds ownership or has invalid digest')
        required = {app_root(other,files)+'/base.apk', HOOKS[other],
                    'product/etc/realme-oem-addons/'+other+'.json',
                    'product/etc/sysconfig/realme-'+other+'.xml'} | SDK_PATHS | lut_paths()
        if not required <= files.keys() or not (SDK_PATHS | lut_paths()) <= own.keys():
            raise ValueError('peer or owner shared runtime is incomplete')
        if (LEGACY_SDK_PATHS & own.keys()) or (LEGACY_SDK_PATHS & files.keys()):
            raise ValueError('legacy SDK layout cannot coexist')
        common = own.keys() & files.keys()
        policy_common = common & oem_runtime_policy.shared_paths()
        if policy_common and policy_common != oem_runtime_policy.shared_paths():
            raise ValueError('partial shared policy transaction')
        if common != required_shared | policy_common:
            raise ValueError('peer shares unexpected files or incomplete runtime')
        if any(own[path] != files[path] for path in common):
            raise ValueError('peer requires a different shared runtime')
        result[version] = dict(profile=other, files=dict(files),
                               shared={path: files[path] for path in sorted(common)})
    return result


def manifest_bytes(files):
    return ''.join(digest+'  '+path+'\n' for path,digest in sorted(files.items())).encode()


def support_members(profile, own, peers):
    """Serialize complete peer manifests; the outer recovery ZIP authenticates them."""
    checked = validate(profile, own, peers)
    if not checked:
        return {}
    members, index = {}, []
    for version, entry in sorted(checked.items()):
        data = manifest_bytes(entry['files'])
        members['peers/'+version+'.sha256'] = data
        index.append(hashlib.sha256(data).hexdigest()+'  '+version+'\n')
    members['peers/index.sha256'] = ''.join(index).encode()
    return members


def inspect_members(profile, own, members):
    """Independently validate archive peer scope, hashes, index and shared subset."""
    if not members:
        return {}
    if 'peers/index.sha256' not in members:
        raise ValueError('peer index missing')
    other = 'photos' if profile == 'camera' else 'camera'
    peers, expected = {}, {'peers/index.sha256'}
    for line in members['peers/index.sha256'].decode().splitlines():
        if not re.fullmatch(r'[0-9a-f]{64}  '+other+r'-[a-z0-9][a-z0-9-]{0,31}', line):
            raise ValueError('invalid peer index row')
        digest, version = line.split('  ')
        if version in peers:
            raise ValueError('duplicate peer version')
        path = 'peers/'+version+'.sha256'
        if path not in members or hashlib.sha256(members[path]).hexdigest() != digest:
            raise ValueError('peer manifest absent or hash differs')
        expected.add(path)
        files = {}
        for row in members[path].decode().splitlines():
            if not re.fullmatch(r'[0-9a-f]{64}  [A-Za-z0-9_./+-]+', row):
                raise ValueError('invalid peer manifest row')
            value, relative = row.split('  ')
            if relative in files:
                raise ValueError('duplicate peer file')
            files[relative] = value
        peers[version] = dict(profile=other, files=files)
    if not peers or set(members) != expected:
        raise ValueError('empty peer index or unindexed peer support')
    return validate(profile, own, peers)
