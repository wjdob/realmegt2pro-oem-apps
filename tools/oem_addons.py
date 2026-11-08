"""Build separate, private RMX3301 OEM app recovery add-ons from pinned stock APKs.

No device operations. Proprietary inputs, generated ZIPs and signing keys stay outside Git.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import ssl
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
import oem_policy
import oem_coinstall
import oem_runtime_policy
import oem_retention
import oem_platform
import oem_installed_space
import oem_small_file_stage

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/oem-addons-23.2.json'
TEMPLATE = ROOT / 'tools/oem_addons/update-binary.sh'
FILTER_ASSET_CONTRACT = ROOT / 'configs/oem-camera-filter-assets-23.2.json'
FILTER_ASSET_ROOT = 'system/etc/realme-lut'
NATIVE_REQUIREMENT_PATHS = frozenset({'system/lib64/libnativewindow.so',
                                    'odm/lib64/libAlgoInterface.so'})
VENDOR_FILTER_CONTRACT = ROOT / 'configs/camera-vendor-filter-23.2.json'
VENDOR_FILTER_CONTRACT37 = ROOT / 'configs/camera-vendor-filter-24.0.json'
VENDOR_FILTER_PATH = 'odm/lib64/libmsnativefilter.so'
PLATFORM_NATIVE_PATHS = frozenset('system/lib64/' + name for name in (
    'libHeifEncoderWrapper.so', 'libNativeWinBuffExchange.so',
    'libHeifWinBufExchg-jni.so', 'liboplusheifwriter.so'))
CAMERA_PRIVILEGED_GRANTS = frozenset({'android.permission.SYSTEM_CAMERA'})
CAMERA_PRIVILEGED_DENIALS = frozenset({
    'android.permission.WRITE_MEDIA_STORAGE', 'android.permission.UPDATE_DEVICE_STATS',
    'android.permission.WRITE_SECURE_SETTINGS', 'android.permission.INTERACT_ACROSS_USERS',
    'android.permission.TURN_SCREEN_ON'})
CAMERA_PRIVILEGED_REQUESTS = CAMERA_PRIVILEGED_GRANTS | CAMERA_PRIVILEGED_DENIALS


def installer_template(profile):
    source=oem_installed_space.prepare(TEMPLATE.read_bytes(),profile)
    source=oem_small_file_stage.prepare(source,profile)
    return oem_platform.template(source, profile, 'installer')


def config_platform(config):
    platform = oem_platform.checked(config)
    if any(key in config and config[key] != platform[key] for key in ('sdk', 'lineage')):
        raise ValueError('optional-app config platform fields disagree')
    return platform


def checked_profile(config, name):
    """Keep a config and all selected profiles within one reviewed generation."""
    platform = config_platform(config)
    profile = dict(config['profiles'][name], id=name, addon_release=config['release'])
    if 'platform' in profile and oem_platform.checked(profile) != platform:
        raise ValueError('optional-app profile/config platform differs')
    return oem_platform.carry(profile, config)


def check_installer_platform(script, profile):
    """Reject opposite-generation scripts before accepting an archive."""
    sdk = str(oem_platform.checked(profile)['sdk']).encode()
    lineage = oem_platform.checked(profile)['lineage'].replace('.', r'\.').encode()
    for prefix, value in ((b'^ro.build.version.sdk=', sdk+b'$'),
                          (b'^ro.lineage.version=', lineage+b'-')):
        if script.count(prefix) != 1 or script.count(prefix+value) != 1:
            raise ValueError('installer platform guards differ from profile')


def check_contract_platform(profile, contract):
    if oem_platform.checked(contract) != oem_platform.checked(profile):
        raise ValueError('optional-app contract platform differs from profile')


def app_partition(profile):
    value = profile.get('app_partition', 'product')
    if value not in ('product', 'system') or (value == 'system' and profile['id'] != 'camera'):
        raise ValueError('unreviewed app placement')
    return value


def app_class(profile):
    value = profile.get('app_class', 'app')
    if value not in ('app', 'priv-app'):
        raise ValueError('unreviewed app class')
    if value == 'priv-app' and (profile['id'] != 'camera'
            or profile['package'] != 'com.oplus.camera' or app_partition(profile) != 'system'):
        raise ValueError('privileged placement is restricted to optional system Camera')
    return value


def app_path(profile):
    if profile.get('content_addressed_app'):
        if (profile['id'] != 'photos' or app_partition(profile) != 'product'
                or app_class(profile) != 'app' or profile['directory'] !=
                'RealmeStockPhotos-'+profile['sha256']):
            raise ValueError('unreviewed content-addressed app directory')
    return app_partition(profile) + '/' + app_class(profile) + '/' + profile['directory']


def legacy_app_path(profile):
    if profile.get('content_addressed_app'):
        app_path(profile)
        previous=profile.get('legacy_gallery_apk_sha256')
        if previous is not None:
            if not re.fullmatch('[0-9a-f]{64}',previous):
                raise ValueError('invalid legacy Gallery APK digest')
            contract=checked_app_compatibility(profile)
            if (not contract or contract['schema'] != 2 or
                    previous not in {v['sha256'] for v in contract['predecessors'].values()}):
                raise ValueError('legacy Gallery directory is not predecessor-bound')
            return 'product/app/RealmeStockPhotos-'+previous
        return 'product/app/RealmeStockPhotos'
    migrate = profile.get('upgrade_from_system_app', False)
    if not isinstance(migrate, bool):
        raise ValueError('legacy Camera relocation must be an explicit boolean')
    if not migrate:
        return ''
    if app_class(profile) != 'priv-app' or profile['directory'] != 'RealmeStockCamera':
        raise ValueError('unreviewed legacy Camera relocation')
    return 'system/app/RealmeStockCamera'


def legacy_app_paths(profile):
    """Exact previous roots from signed-identity/profile bindings, never a glob."""
    roots = {legacy_app_path(profile)} - {''}
    if profile.get('content_addressed_app'):
        contract = checked_app_compatibility(profile)
        if contract and contract['schema'] == 2:
            roots.update('product/app/RealmeStockPhotos-' + identity['sha256']
                         for identity in contract['predecessors'].values())
    roots.discard(app_path(profile))
    return sorted(roots)


def privileged_permissions(profile):
    spec = profile.get('privileged_permissions')
    if app_class(profile) != 'priv-app':
        if spec:
            raise ValueError('privileged permissions require privileged placement')
        return None
    if (not isinstance(spec, dict) or set(spec) != {'grant', 'deny'}
            or not isinstance(spec['grant'], list) or not isinstance(spec['deny'], list)
            or len(spec['grant']) != len(set(spec['grant']))
            or len(spec['deny']) != len(set(spec['deny']))
            or not CAMERA_PRIVILEGED_GRANTS <= set(spec['grant'])
            or set(spec['grant']) & set(spec['deny'])
            or set(spec['grant']) | set(spec['deny']) != CAMERA_PRIVILEGED_REQUESTS):
        raise ValueError('unreviewed Camera privileged permission grant/denial set')
    root = ET.Element('permissions')
    package = ET.SubElement(root, 'privapp-permissions', package=profile['package'])
    for tag, names in [('permission', spec['grant']), ('deny-permission', spec['deny'])]:
        for name in sorted(names):
            ET.SubElement(package, tag, name=name)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(path):
    p = PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts or not re.fullmatch(r'[A-Za-z0-9_./+-]+', path):
        raise ValueError('unsafe relative path: ' + path)
    return path


def legacy_shared_paths(profile):
    paths = profile.get('legacy_shared_paths', ())
    if paths and (profile['id'] != 'camera' or app_partition(profile) != 'system'
                  or not isinstance(paths, list) or len(paths) != len(set(paths))
                  or set(paths) != oem_coinstall.LEGACY_SDK_PATHS
                  or not oem_coinstall.SDK_PATHS <= set(profile.get('shared_paths', ()))):
        raise ValueError('unreviewed shared SDK relocation')
    return paths


def load_peer_history(profile):
    spec = profile.get('peer_history')
    if not spec:
        return {}
    path = ROOT / safe_path(spec['source'])
    if (not path.resolve().is_relative_to((ROOT/'configs').resolve())
            or sha(path.read_bytes()) != spec['sha256']):
        raise ValueError('unreviewed peer history')
    history = json.loads(path.read_text())
    check_contract_platform(profile, history)
    if (set(history) - {'platform'} != {'schema', 'profiles'} or history['schema'] != 1
            or set(history['profiles']) != {'camera', 'photos'}):
        raise ValueError('peer history schema differs')
    peers = history['profiles'][profile['id']]
    if not peers:
        raise ValueError('peer history is empty')
    return peers


def validate_gallery_runtime(profile, manifest):
    """Keep the dependency-complete Photos profile from regressing to APK-only."""
    if profile['id'] != 'photos' or not profile.get('peer_history'):
        if profile.get('runtime_contract'):
            raise ValueError('unexpected Gallery runtime contract')
        return
    spec = profile.get('runtime_contract')
    if not isinstance(spec, dict) or set(spec) != {'source', 'sha256'}:
        raise ValueError('Gallery shared-runtime contract is required')
    path = ROOT / safe_path(spec['source'])
    if (not path.resolve().is_relative_to((ROOT/'configs').resolve())
            or sha(path.read_bytes()) != spec['sha256']):
        raise ValueError('unreviewed Gallery runtime contract')
    contract = json.loads(path.read_text())
    check_contract_platform(profile, contract)
    if (contract['schema'] != 1 or app_partition(profile) != 'product'
            or app_class(profile) != 'app' or (profile.get('policy') and
                profile['policy'].get('kind') != oem_runtime_policy.KIND)
            or profile.get('platform_native') or profile.get('privileged_permissions')
            or not (profile.get('explicit_reinstall_after_ota') or oem_retention.checked_spec(profile))):
        raise ValueError('unreviewed Gallery runtime placement or privileges')
    for key in ('package', 'version', 'sha256', 'certificate_sha256'):
        if profile[key] != contract['app'][key]:
            raise ValueError('Gallery application identity differs from runtime contract')
    if (profile.get('native_requirements') != contract['native_requirements']
            or profile.get('platform_framework_sha256') != contract['platform_framework_sha256']
            or sha(FILTER_ASSET_CONTRACT.read_bytes()) != contract['filter_asset_contract_sha256']):
        raise ValueError('Gallery platform/asset contract differs')
    native = {s['name']: s['sha256'] for s in profile.get('extra_native', ())}
    if native != contract['extra_native'] or len(native) != len(profile.get('extra_native', ())):
        raise ValueError('Gallery SDK native providers are incomplete or duplicated')
    expected = {app_path(profile)+'/base.apk': contract['app']['sha256']}
    expected.update({app_path(profile)+'/lib/arm64/'+n: digest for n,digest in native.items()})
    sdk = contract['sdk']
    expected_shared = oem_coinstall.SDK_PATHS
    if profile.get('policy',{}).get('kind') == oem_runtime_policy.KIND:
        expected_shared |= oem_runtime_policy.shared_paths()
        if contract.get('shared_policy_manifest_sha256') != profile['policy']['sha256']:
            raise ValueError('Gallery shared policy is not runtime-bound')
    if set(profile.get('shared_paths', ())) != expected_shared:
        raise ValueError('Gallery SDK registration placement differs')
    expected[sdk['jar_path']] = sdk['sha256']
    expected[sdk['xml_path']] = sha(('<?xml version="1.0" encoding="utf-8"?>\n<permissions>\n'
        '  <library name="'+sdk['library']+'" file="/'+sdk['jar_path']+'" />\n</permissions>\n').encode())
    expected.update(camera_assets(profile))
    if len(camera_assets(profile)) != 71 or any(manifest.get(p) != digest for p,digest in expected.items()):
        raise ValueError('Gallery SDK/APK/JNI/LUT payload is incomplete or differs')


def camera_assets(profile):
    specs = profile.get('extra_assets', ())
    if not specs:
        return {}
    eligible = ((profile['id'] == 'camera' and profile['package'] == 'com.oplus.camera'
                 and app_partition(profile) == 'system')
                or (profile['id'] == 'photos' and profile['package'] == 'com.coloros.gallery3d'
                    and app_partition(profile) == 'product' and profile.get('peer_history')
                    and oem_coinstall.SDK_PATHS <= set(profile.get('shared_paths', ()))))
    if not eligible:
        raise ValueError('filter assets are restricted to optional system Camera')
    contract = json.loads(FILTER_ASSET_CONTRACT.read_text())
    expected = {FILTER_ASSET_ROOT + '/' + name: row['sha256']
                for name, row in contract['files'].items()}
    if (contract['asset_root'] != FILTER_ASSET_ROOT
            or len(specs) != len(expected)
            or {s['path']: s['sha256'] for s in specs} != expected):
        raise ValueError('filter assets differ from the complete pinned inventory')
    for path in expected:
        safe_path(path)
        if '/' in path[len(FILTER_ASSET_ROOT) + 1:]:
            raise ValueError('nested filter asset destination')
    return expected


def native_requirements(profile):
    requirements = profile.get('native_requirements', {})
    if not requirements:
        return b''
    eligible = ((profile['id'] == 'camera' and profile.get('policy')
                 and app_partition(profile) == 'system')
                or (profile['id'] == 'photos' and profile.get('peer_history')
                    and (not profile.get('policy') or profile['policy'].get('kind') == oem_runtime_policy.KIND)
                    and app_partition(profile) == 'product'))
    expected_paths = NATIVE_REQUIREMENT_PATHS
    if profile['id'] == 'photos' and profile.get('policy',{}).get('kind') == oem_runtime_policy.KIND:
        expected_paths |= {oem_runtime_policy.EDITOR_BACKEND_PATH}
        if requirements.get(oem_runtime_policy.EDITOR_BACKEND_PATH) != oem_runtime_policy.editor_backend(profile):
            raise ValueError('Gallery editor backend differs')
    vendor_spec = profile.get('vendor_filter_contract')
    api37 = oem_platform.checked(profile) == oem_platform.ANDROID17
    if api37 and not vendor_spec:
        raise ValueError('API37 requires an explicit vendor filter cohort')
    if vendor_spec:
        contract_path = VENDOR_FILTER_CONTRACT37 if api37 else VENDOR_FILTER_CONTRACT
        expected = dict(source=str(contract_path.relative_to(ROOT)),
                        sha256=sha(contract_path.read_bytes()))
        if vendor_spec != expected:
            raise ValueError('unreviewed vendor filter runtime contract')
        contract = json.loads(contract_path.read_text())
        if api37:
            check_contract_platform(profile, contract)
            context = oem_runtime_policy.context_for_profile(profile)
            if contract.get('target_files_sha256') != context.config['target_files_sha256']:
                raise ValueError('API37 vendor and policy base cohorts differ')
        if (requirements.get(VENDOR_FILTER_PATH) != contract['provider']['sha256']
                or requirements.get('odm/lib64/libAlgoInterface.so') != contract['aps']['sha256']):
            raise ValueError('vendor filter provider/APS binding differs')
        expected_paths = expected_paths | {VENDOR_FILTER_PATH}
    if (not eligible or set(requirements) != expected_paths
            or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                   for value in requirements.values())):
        raise ValueError('unreviewed Camera native runtime requirements')
    return ''.join(digest+'  '+path+'\n' for path,digest in sorted(requirements.items())).encode()


def allowed(profile, rel):
    safe_path(rel)
    if rel.startswith(FILTER_ASSET_ROOT + '/'):
        return rel in camera_assets(profile)
    if rel.startswith('system/lib64/'):
        return (profile['id'] == 'camera' and app_partition(profile) == 'system'
                and rel in PLATFORM_NATIVE_PATHS and rel in profile.get('shared_paths', ()))
    if ((profile['id'] == 'camera' or profile.get('policy',{}).get('kind') == oem_runtime_policy.KIND) and profile.get('policy')
            and (rel in oem_policy.PATCH_PATHS or rel.startswith(oem_policy.STORAGE))):
        return True
    roots = [app_path(profile), *legacy_app_paths(profile)]
    if rel == 'system/etc/permissions/privapp-permissions-realme-camera.xml':
        return profile['id'] == 'camera' and app_class(profile) == 'priv-app'
    return (any(rel.startswith(root + '/') for root in roots)
            or rel in profile.get('shared_paths', ()) or rel in legacy_shared_paths(profile)
            or rel in ('product/etc/sysconfig/realme-' + profile['id'] + '.xml',
                       'product/etc/realme-oem-addons/' + profile['id'] + '.json',
                       'system/addon.d/' + profile['hook']))


def hook(profile, paths):
    if oem_retention.checked_spec(profile):
        manifest = {path: sha(data) for path, data in paths.items()}
        descriptor = oem_retention.descriptor(profile, manifest, paths)
        return oem_retention.render_unhooked(descriptor)
    if profile.get('explicit_reinstall_after_ota'):
        if profile['id'] != 'photos' or not profile.get('peer_history'):
            raise ValueError('unreviewed explicit add-on retention policy')
        return b'''#!/sbin/sh
# ADDOND_VERSION=3
# Shared SDK/LUT compatibility must be revalidated after the base ROM update.
case "$1" in
  backup) echo 'Realme Photos: reinstall a matching optional bundle after OTA merge completes.' ;;
esac
'''
    if profile.get('policy'):
        # Do not carry an old policy snapshot into a new ROM. Retention requires
        # validation of the new slot's complete CIL input set, including ODM.
        return b'''#!/sbin/sh
# ADDOND_VERSION=3
# This experimental Camera policy bundle requires explicit reinstall after OTA.
# No APK, SDK, policy, signer map or app data is backed up or restored here.
case "$1" in
  backup) echo 'Realme Camera: automatic retention disabled; reinstall a compatible add-on after OTA.' ;;
esac
'''
    # V3 maps $S/product paths through get_output_path to the new A/B product mount.
    filelist = '\n'.join(sorted(p for p in paths if p.startswith('product/')))
    return ('''#!/sbin/sh
# ADDOND_VERSION=3
# Optional RMX3301 profile; keep independent of GApps and other OEM profiles.
. /tmp/backuptool.functions
list_files() {
cat <<'REALME_FILES'
''' + filelist + '''
REALME_FILES
}
case "$1" in
  backup)
    list_files | while IFS= read -r rel; do backup_file "$S/$rel"; done
    ;;
  restore)
    list_files | while IFS= read -r rel; do
      if [ -f "$C/$S/$rel" ]; then restore_file "$S/$rel"; fi
    done
    ;;
  post-restore)
    owned_root=$(get_output_path "$S/product/app/''' + profile['directory'] + '''")
    list_files | while IFS= read -r rel; do
      dest=$(get_output_path "$S/$rel")
      [ -f "$dest" ] || exit 1
      chown 0:0 "$dest" || exit 1
      chmod 0644 "$dest" || exit 1
      chcon u:object_r:system_file:s0 "$dest" || exit 1
      parent="${dest%/*}"
      while :; do
        case "$parent" in
          "$owned_root"|"$owned_root/"*)
            chown 0:0 "$parent" && chmod 0755 "$parent" &&
              chcon u:object_r:system_file:s0 "$parent" || exit 1
            parent="${parent%/*}" ;;
          *) break ;;
        esac
      done
    done
    ;;
esac
''').encode()


def payload(profile, apk, shared=None, extra_native=None, policy=None, platform_native=None,
            extra_assets=None):
    retention = oem_retention.checked_spec(profile)
    app = app_path(profile)
    permission_xml = privileged_permissions(profile)
    result = {app + '/base.apk': apk}
    if permission_xml:
        result['system/etc/permissions/privapp-permissions-realme-camera.xml'] = permission_xml
    with zipfile.ZipFile(io.BytesIO(apk)) as z:
        if len(z.namelist()) != len(set(z.namelist())):
            raise ValueError('duplicate APK ZIP members')
        total = 0
        for info in z.infolist():
            if not info.filename.startswith('lib/arm64-v8a/') or not info.filename.endswith('.so'):
                continue
            name = info.filename[len('lib/arm64-v8a/'):]
            if '/' in name or not re.fullmatch(r'[A-Za-z0-9_.+-]+\.so', name):
                raise ValueError('unexpected JNI path')
            total += info.file_size
            if total > 512 * 1024**2:
                raise ValueError('JNI size exceeds reviewed bound')
            data = z.read(info)
            if data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183:
                raise ValueError('expected AArch64 ELF64 JNI')
            result[app + '/lib/arm64/' + name] = data
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n<config>\n'
           '  <hidden-api-whitelisted-app package="' + profile['package'] + '" />\n</config>\n')
    result['product/etc/sysconfig/realme-' + profile['id'] + '.xml'] = xml.encode()
    receipt = {k: profile[k] for k in ('id', 'package', 'version', 'sha256', 'certificate_sha256')}
    oem_platform.carry(receipt, profile)
    compatibility = checked_app_compatibility(profile)
    if compatibility:
        receipt['app_compatibility_contract_sha256'] = profile['app_compatibility']['sha256']
        receipt['source_apk_sha256'] = compatibility['source_app']['sha256']
        receipt['signer_role'] = compatibility['signer_role']
    if permission_xml:
        receipt['app_class'] = 'priv-app'
        receipt['privileged_permissions_sha256'] = sha(permission_xml)
    if 'addon_release' in profile:
        receipt['addon_release'] = profile['addon_release']
    if profile.get('runtime_contract'):
        receipt['runtime_contract_sha256'] = profile['runtime_contract']['sha256']
    if profile.get('explicit_reinstall_after_ota'):
        receipt['ota_retention'] = 'disabled; reinstall a compatible add-on after OTA'
    native_receipt = {}
    for spec, data in extra_native or ():
        name = spec['name']
        if not re.fullmatch(r'lib[A-Za-z0-9_.+-]+\.so', name):
            raise ValueError('unexpected extra native basename')
        if sha(data) != spec['sha256']:
            raise ValueError('extra native input hash mismatch')
        if (len(data) < 64 or len(data) > 128 * 1024**2
                or data[:6] != b'\x7fELF\x02\x01'
                or int.from_bytes(data[18:20], 'little') != 183):
            raise ValueError('expected bounded AArch64 ELF64 extra native input')
        target = app + '/lib/arm64/' + name
        if target in result:
            raise ValueError('extra native input conflicts with APK or another input')
        result[target] = data
        native_receipt[name] = spec['sha256']
    if native_receipt:
        receipt['extra_native'] = native_receipt
    expected_assets = camera_assets(profile)
    supplied_assets = {}
    for spec, data in extra_assets or ():
        path = spec['path']
        if (path not in expected_assets or path in supplied_assets or path in result
                or not 1 <= len(data) <= 4 * 1024**2
                or spec['sha256'] != expected_assets[path] or sha(data) != spec['sha256']):
            raise ValueError('unreviewed Camera filter asset input')
        supplied_assets[path] = data
    if set(supplied_assets) != set(expected_assets):
        raise ValueError('incomplete Camera filter asset payload')
    result.update(supplied_assets)
    if supplied_assets:
        receipt['filter_assets'] = expected_assets
    if native_requirements(profile):
        receipt['native_requirements'] = profile['native_requirements']
    for spec, data in platform_native or ():
        path = spec['path']
        if (profile['id'] != 'camera' or app_partition(profile) != 'system'
                or path not in profile.get('shared_paths', ())
                or path not in PLATFORM_NATIVE_PATHS
                or path in result or sha(data) != spec['sha256']
                or data[:6] != b'\x7fELF\x02\x01'
                or int.from_bytes(data[18:20], 'little') != 183):
            raise ValueError('unreviewed Camera platform native provider')
        result[path] = data
        receipt.setdefault('platform_native', {})[path] = spec['sha256']
    if shared:
        spec, data = shared
        if profile['id'] not in spec.get('compatible_profiles', ('camera', 'photos')):
            raise ValueError('shared SDK compatibility is not reviewed for this profile')
        if sha(data) != spec['sha256']:
            raise ValueError('shared SDK input hash mismatch')
        result[spec['jar_path']] = data
        result[spec['xml_path']] = ('<?xml version="1.0" encoding="utf-8"?>\n<permissions>\n'
            '  <library name="' + spec['library'] + '" file="/' + spec['jar_path'] + '" />\n'
            '</permissions>\n').encode()
        receipt['shared_library'] = spec['library']
        receipt['shared_sdk_sha256'] = spec['sha256']
    if bool(profile.get('policy')) != bool(policy):
        raise ValueError('policy profile/input mismatch')
    if policy:
        manifest, policy_files = policy
        if oem_platform.checked(manifest) != oem_platform.checked(profile):
            raise ValueError('policy platform differs from app profile')
        runtime_policy = profile['policy'].get('kind') == oem_runtime_policy.KIND
        context = oem_runtime_policy.context_for_profile(profile)
        if context is not None and (not runtime_policy
                or manifest.get('base_config_sha256') != context.expected_sha256
                or manifest.get('target_files_sha256') != context.config['target_files_sha256']):
            raise ValueError('API37 policy base context differs from app profile')
        if runtime_policy:
            if (profile['id'],profile['package'],profile['certificate_sha256']) not in (
                ('camera',oem_runtime_policy.CAMERA_PACKAGE,oem_runtime_policy.CAMERA_CERT_SHA),
                ('photos',oem_runtime_policy.GALLERY_PACKAGE,oem_runtime_policy.GALLERY_CERT_SHA)):
                raise ValueError('shared policy app identity differs')
            if manifest.get('kind') != oem_runtime_policy.KIND:
                raise ValueError('shared policy payload kind differs')
        elif profile['id'] != 'camera' or profile['package'] != 'com.oplus.camera':
            raise ValueError('policy is restricted to the Camera profile')
        if set(result) & set(policy_files):
            raise ValueError('policy payload collides with app files')
        result.update(policy_files)
        receipt['policy_manifest_sha256'] = profile['policy']['sha256']
        if context is not None:
            receipt['policy_base_context_sha256'] = context.expected_sha256
        receipt['ota_retention'] = 'disabled; reinstall a compatible add-on after OTA'
    if retention:
        receipt['ota_retention'] = retention
        receipt['platform_framework_sha256'] = profile['platform_framework_sha256']
    result['product/etc/realme-oem-addons/' + profile['id'] + '.json'] = (
        json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode()
    result['system/addon.d/' + profile['hook']] = hook(profile, result)
    if not all(allowed(profile, p) for p in result):
        raise ValueError('payload exceeds profile ownership')
    return result


def checked_app_compatibility(profile):
    """Allow an explicit Gallery APK transition without a general signer escape."""
    spec = profile.get('app_compatibility')
    if spec is None:
        return None
    if (profile['id'] != 'photos' or profile['package'] != 'com.coloros.gallery3d'
            or app_partition(profile) != 'product' or app_class(profile) != 'app'
            or (profile.get('policy') and profile['policy'].get('kind') != oem_runtime_policy.KIND)
            or profile.get('privileged_permissions')
            or profile.get('platform_native') or not profile.get('runtime_contract')
            or not isinstance(spec, dict) or set(spec) != {'source', 'sha256'}):
        raise ValueError('unreviewed application compatibility profile')
    path = ROOT / safe_path(spec['source'])
    if (not path.resolve().is_relative_to((ROOT/'configs').resolve())
            or sha(path.read_bytes()) != spec['sha256']):
        raise ValueError('unreviewed application compatibility contract')
    contract = json.loads(path.read_text())
    check_contract_platform(profile, contract)
    source = dict(sha256='ce058185478efe7faed94ffbae42bc640741e33744963dcf1afd93f7f693c20e',
                  certificate_sha256='64aafaf1d5bc9155a9e417a849e4f8eda1d0d1341667c28ed7c443c76f820b9a',
                  min_sdk=31, target_sdk=34)
    target = dict(sha256=profile['sha256'], certificate_sha256=profile['certificate_sha256'],
                  min_sdk=31, target_sdk=34)
    if (contract['schema'] not in (1,2) or contract['package'] != profile['package']
            or contract['source_app'] != source or contract['prepared_app'] != target
            or contract['flavor'] != 'lineage'
            or contract['signer_role'] != 'dedicated-optional-gallery-app'
            or source['certificate_sha256'] == target['certificate_sha256']
            or not re.fullmatch('[0-9a-f]{64}', target['certificate_sha256'])):
        raise ValueError('Gallery compatibility identities or SDK policy differ')
    predecessors = contract['predecessors']
    if (not predecessors or len(predecessors) > 8
            or any(not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}', version) for version in predecessors)):
        raise ValueError('unreviewed Gallery app predecessor identity')
    if contract['schema']==1:
        if any(identity not in (source,target) for identity in predecessors.values()):
            raise ValueError('unreviewed Gallery app predecessor identity')
    else:
        bindings=contract.get('predecessor_profiles',{})
        if (set(bindings)!=set(predecessors) or target['certificate_sha256']!=oem_runtime_policy.GALLERY_CERT_SHA):
            raise ValueError('Gallery predecessor profiles must bind the dedicated signer')
        for version,identity in predecessors.items():
            binding=bindings[version]
            if not isinstance(binding,dict) or set(binding)!={'source','sha256'}:
                raise ValueError('invalid Gallery predecessor profile binding')
            previous_path=ROOT/safe_path(binding['source'])
            if (not previous_path.resolve().is_relative_to((ROOT/'configs').resolve())
                    or sha(previous_path.read_bytes())!=binding['sha256']):
                raise ValueError('Gallery predecessor profile changed')
            previous=json.loads(previous_path.read_text())
            platform = oem_platform.checked(profile)
            if (previous.get('release')!=version or previous.get('sdk')!=platform['sdk'] or
                    previous.get('lineage')!=platform['lineage'] or previous.get('model')!='RMX3301'
                    or set(previous.get('profiles',{}))!={'photos'}):
                raise ValueError('unreviewed predecessor platform or profile')
            app=previous['profiles']['photos']
            expected=dict(sha256=app['sha256'],certificate_sha256=app['certificate_sha256'],min_sdk=31,target_sdk=34)
            if (identity!=expected or app['package']!=profile['package']
                    or app['certificate_sha256']!=target['certificate_sha256']
                    or app.get('app_partition','product')!='product' or app.get('app_class','app')!='app'
                    or not app.get('content_addressed_app') or app['directory']!='RealmeStockPhotos-'+app['sha256']):
                raise ValueError('Gallery predecessor app identity or placement differs')
            spec=app.get('app_compatibility',{});old_contract=ROOT/safe_path(spec['source'])
            if (not old_contract.resolve().is_relative_to((ROOT/'configs').resolve())
                    or sha(old_contract.read_bytes())!=spec['sha256']):
                raise ValueError('Gallery predecessor compatibility recipe changed')
            old_identity=json.loads(old_contract.read_text())
            if (old_identity.get('prepared_app')!=identity or old_identity.get('package')!=profile['package']
                    or old_identity.get('signer_role')!='dedicated-optional-gallery-app'):
                raise ValueError('Gallery predecessor recipe does not approve that APK')
    policyless = contract.get('policyless_predecessors', [])
    if (not isinstance(policyless, list) or len(policyless) != len(set(policyless))
            or not set(policyless) <= predecessors.keys()
            or (policyless and (not profile.get('content_addressed_app') or
                profile.get('policy',{}).get('kind') != oem_runtime_policy.KIND))):
        raise ValueError('unreviewed policyless Gallery transition')
    return contract


def checked_upgrade_history(profile, files, history):
    check_contract_platform(profile, history)
    if history['package'] != profile['package'] or history['certificate_sha256'] != profile['certificate_sha256']:
        raise ValueError('predecessor history identity mismatch')
    contract = checked_app_compatibility(profile)
    if contract is None:
        if history['schema'] != 1:
            raise ValueError('unreviewed predecessor history schema')
    elif (history['schema'] != 2 or history.get('app_compatibility_contract_sha256')
            != profile['app_compatibility']['sha256']):
        raise ValueError('Gallery signer transition history is not explicitly bound')
    return checked_upgrades(profile, files, history['versions'])


def checked_upgrades(profile, files, upgrades):
    """Bound historical installs to this profile and the same policy baseline."""
    result = {}
    if upgrades and profile['id'] not in ('camera', 'photos'):
        raise ValueError('unreviewed upgrade profile')
    if len(upgrades or {}) > 8:
        raise ValueError('too many upgrade predecessors')
    compatibility = checked_app_compatibility(profile)
    for version, manifest in (upgrades or {}).items():
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}', version) or not manifest or len(manifest) > 512:
            raise ValueError('invalid predecessor version or file count')
        for path, digest in manifest.items():
            if not allowed(profile, path) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('predecessor exceeds Camera ownership')
        roots = [app_path(profile), *legacy_app_paths(profile)]
        apks = [root + '/base.apk' for root in roots]
        found_apks = [path for path in apks if path in manifest]
        expected_apk = profile['sha256']
        if compatibility and version in compatibility['predecessors']:
            expected_apk = compatibility['predecessors'][version]['sha256']
        if len(found_apks) != 1 or manifest[found_apks[0]] != expected_apk:
            raise ValueError('predecessor APK identity is not reviewed')
        selected_root = found_apks[0].rsplit('/', 1)[0]
        if any(path.startswith(root + '/') for root in roots if root != selected_root
               for path in manifest):
            raise ValueError('predecessor mixes application directories')
        if profile.get('policy'):
            policyless = (profile['id'] == 'photos' and
                profile['policy'].get('kind') == oem_runtime_policy.KIND and
                compatibility and version in compatibility.get('policyless_predecessors', ()))
            if policyless:
                if oem_runtime_policy.shared_paths() & manifest.keys():
                    raise ValueError('policyless predecessor contains policy files')
                result[version] = dict(manifest)
                continue
            required = {oem_policy.STORAGE + 'inputs.sha256'}
            required |= {oem_policy.STORAGE + 'preimages/' + Path(p).name
                         for p in oem_policy.PATCH_PATHS}
            if any(manifest.get(p) != sha(files[p]) for p in required):
                raise ValueError('predecessor policy baseline differs from target')
            if not oem_policy.PATCH_PATHS <= set(manifest):
                raise ValueError('predecessor policy transaction is incomplete')
        result[version] = dict(manifest)
    return result


def install_budget(files, previous=None):
    """Reserve complete new copies of changed files; never credit freed extents."""
    selected = {path: data for path, data in files.items()
                if previous is None or previous.get(path) != sha(data)}
    return {part: (sum(len(data) for path, data in selected.items()
                       if path.startswith(part + '/')) + 1023) // 1024 + margin
            for part, margin in [('system', 4096), ('product', 16384)]}


def recovery_support(profile, helper):
    spec = profile.get('recovery_helper')
    if not spec:
        if helper is not None:
            raise ValueError('unexpected recovery helper')
        return {}, '', ''
    if (not isinstance(helper, bytes) or sha(helper) != spec['sha256']
            or not 4096 <= len(helper) <= 256 * 1024
            or helper[:6] != b'\x7fELF\x02\x01'
            or int.from_bytes(helper[18:20], 'little') != 183):
        raise ValueError('unreviewed recovery helper')
    bindings = spec.get('libraries', {})
    if set(bindings) != {'libfs_mgr.so', 'liblp.so', 'libbase.so', 'libc++.so', 'libc.so', 'libdl.so'}:
        raise ValueError('incomplete recovery API binding')
    for digest in bindings.values():
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid recovery API digest')
    manifest = ''.join(digest + '  /system/lib64/' + name + '\n'
                       for name, digest in sorted(bindings.items())).encode()
    return {'recovery/prepare-logical': helper, 'recovery/libraries.sha256': manifest}, sha(helper), sha(manifest)


def archive_bytes(profile, files, action, certificate, upgrades=None, recovery_helper=None):
    retention = oem_retention.checked_spec(profile)
    if retention:
        own_hook = 'system/addon.d/' + profile['hook']
        expected_hook = hook(profile, {p: data for p, data in files.items() if p != own_hook})
        if files.get(own_hook) != expected_hook:
            raise ValueError('retention hook differs from payload contract')
    expected_assets = camera_assets(profile)
    if expected_assets and not native_requirements(profile):
        raise ValueError('filter assets require exact native runtime bindings')
    if {p: sha(data) for p, data in files.items()
            if p.startswith(FILTER_ASSET_ROOT + '/')} != expected_assets:
        raise ValueError('archive filter assets differ from pinned inventory')
    if action not in ('install', 'remove', 'check'):
        raise ValueError('invalid action')
    permission_path = 'system/etc/permissions/privapp-permissions-realme-camera.xml'
    expected_permissions = privileged_permissions(profile)
    if expected_permissions is not None:
        if files.get(permission_path) != expected_permissions:
            raise ValueError('Camera privileged permission payload differs from reviewed policy')
    elif permission_path in files:
        raise ValueError('unexpected privileged permission payload')
    framework_sha = profile.get('platform_framework_sha256', '')
    if framework_sha and ((app_partition(profile) != 'system'
                           and not (profile['id'] == 'photos' and profile.get('peer_history')))
                          or not re.fullmatch('[0-9a-f]{64}', framework_sha)):
        raise ValueError('invalid platform framework binding')
    # PEM whitespace normalization matches the recovery check; recovery itself verifies ZIP trust.
    cert_hash = sha(re.sub(rb'\s+', b'', certificate))
    isolated_photos = profile.get('isolated_photos_alpha', False)
    if not isinstance(isolated_photos, bool) or (isolated_photos and
            (profile['id'] != 'photos' or profile.get('shared_paths') or profile.get('policy'))):
        raise ValueError('unreviewed isolated Photos profile')
    support, helper_sha, bindings_sha = recovery_support(profile, recovery_helper)
    own = {path: sha(data) for path, data in files.items()}
    validate_gallery_runtime(profile, own)
    peers = load_peer_history(profile)
    peer_support = oem_coinstall.support_members(profile['id'], own, peers) if peers else {}
    peer_profile = 'photos' if profile['id'] == 'camera' else 'camera'
    peer_paths = {path for entry in peers.values() for path in entry['files']}
    values = dict(PROFILE=profile['id'], TITLE=profile['title'], ACTION=action,
                  HELPER_SHA=helper_sha, HELPER_LIBRARIES_SHA=bindings_sha,
                  ISOLATED_PHOTOS='1' if isolated_photos else '0',
                  HAS_PEERS='1' if peer_support else '0',
                  PEER_INDEX_SHA=sha(peer_support['peers/index.sha256']) if peer_support else '',
                  PEER_PROFILE=peer_profile,
                  PEER_APP_ROOT=' '.join(sorted({oem_coinstall.app_root(peer_profile,entry['files'])
                                        for entry in peers.values()})) if peer_support else oem_coinstall.APP_ROOTS[peer_profile],
                  PEER_ALLOWED_CASE='|'.join(sorted(peer_paths)) or '__no_peer_files__',
                  SHARED_RUNTIME_CASE='|'.join(sorted(oem_coinstall.shareable_paths()))
                                      if peer_support else '__no_shared_runtime__',
                  LEGACY_SHARED_CASE='|'.join(legacy_shared_paths(profile)) or '__no_legacy_shared_files__',
                  APP_PARTITION=app_partition(profile),
                  APP_CLASS=app_class(profile), LEGACY_APP_ROOT=' '.join(legacy_app_paths(profile)),
                  PLATFORM_FRAMEWORK_SHA=profile.get('platform_framework_sha256', ''),
                  APPDIR=profile['directory'], HOOK=profile['hook'], CERT_SHA=cert_hash,
                  OTHER_APPDIR=profile.get('other_directory', '__no_other_profile__'),
                  SHARED_CASE='|'.join(safe_path(p) for p in profile.get('shared_paths', ()))
                              or '__no_shared_files__',
                  PRODUCT_KIB=str((sum(len(v) for p, v in files.items()
                                      if p.startswith('product/')) + 1023) // 1024 + 16384),
                  SYSTEM_KIB=str((sum(len(v) for p, v in files.items()
                                     if p.startswith('system/')) + 1023) // 1024 + 4096))
    values['HAS_POLICY'] = '1' if profile.get('policy') else '0'
    values['SHARED_POLICY'] = '1' if profile.get('policy',{}).get('kind') == oem_runtime_policy.KIND else '0'
    assets = camera_assets(profile)
    values['HAS_ASSETS'] = '1' if assets else '0'
    values['ASSET_CASE'] = '|'.join(sorted(assets)) or '__no_filter_assets__'
    requirements = native_requirements(profile)
    values['HAS_NATIVE_REQUIREMENTS'] = '1' if requirements else '0'
    values['NATIVE_REQUIREMENT_COUNT'] = str(len(profile.get('native_requirements', {})))
    upgrades = checked_upgrades(profile, files, upgrades if action != 'remove' else None)
    values['HAS_UPGRADES'] = '1' if upgrades else '0'
    script = installer_template(profile).decode('utf-8')
    for name, value in values.items():
        if "'" in value or '\n' in value:
            raise ValueError('unsafe template value')
        script = script.replace('@' + name + '@', value)
    if re.search(r'@[A-Z_]+@', script):
        raise ValueError('unexpanded installer variable')
    manifest = ''.join(sha(data) + '  ' + path + '\n' for path, data in sorted(files.items())).encode()
    members = {'META-INF/com/google/android/update-binary': script.encode(),
               'META-INF/com/google/android/updater-script': b'# Independent optional OEM profile\n',
               'manifest.sha256': manifest}
    members.update(support)
    members.update(peer_support)
    if requirements:
        members['native-requirements.sha256'] = requirements
    if profile.get('policy'):
        # Also needed by check/remove and rollback: never depend on an installed
        # preimage file that removal might have already deleted.
        members.update({'policy/' + p[len(oem_policy.STORAGE):]: data
                        for p, data in files.items() if p.startswith(oem_policy.STORAGE)})
    if upgrades:
        index = []
        for version, previous in sorted(upgrades.items()):
            data = ''.join(digest + '  ' + path + '\n'
                           for path, digest in sorted(previous.items())).encode()
            members['upgrades/' + version + '.sha256'] = data
            budget = install_budget(files, previous)
            members['upgrades/' + version + '.space'] = (
                str(budget['system']) + ' ' + str(budget['product']) + '\n').encode()
            index.append(sha(data) + '  ' + version + '\n')
        members['upgrades/index.sha256'] = ''.join(index).encode()
    if action == 'install':
        members.update({'payload/' + path: data for path, data in files.items()})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path, data in sorted(members.items()):
            safe_path(path)
            info = zipfile.ZipInfo(path, (2009, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if path.endswith('update-binary') else 0o100644) << 16
            z.writestr(info, data)
    return out.getvalue()


def inspect_archive(path, profile):
    retention = oem_retention.checked_spec(profile)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('duplicate ZIP members')
        for name in names:
            safe_path(name)
        recovery_members = {n for n in names if n.startswith('recovery/')}
        helper = z.read('recovery/prepare-logical') if recovery_members else None
        support, helper_sha, bindings_sha = recovery_support(profile, helper)
        if recovery_members != set(support) or any(z.read(n) != data for n, data in support.items()):
            raise ValueError('recovery support differs')
        installer = z.read('META-INF/com/google/android/update-binary')
        check_installer_platform(installer, profile)
        receipt_path = 'payload/product/etc/realme-oem-addons/' + profile['id'] + '.json'
        if receipt_path in names:
            receipt = json.loads(z.read(receipt_path))
            if oem_platform.checked(receipt) != oem_platform.checked(profile):
                raise ValueError('payload receipt platform differs from profile')
        for name, value in (('HELPER_SHA', helper_sha), ('HELPER_LIBRARIES_SHA', bindings_sha)):
            if (name + "='" + value + "'").encode() not in installer.splitlines():
                raise ValueError('installer recovery binding differs')
        flag = b"ISOLATED_PHOTOS='" + (b'1' if profile.get('isolated_photos_alpha') else b'0') + b"'"
        if flag not in installer.splitlines():
            raise ValueError('installer Photos isolation differs')
        required = native_requirements(profile)
        if required:
            if z.read('native-requirements.sha256') != required:
                raise ValueError('native runtime binding differs')
            count_line = ("NATIVE_REQUIREMENT_COUNT='"+str(len(profile['native_requirements']))+"'").encode()
            if (profile.get('vendor_filter_contract') or b'NATIVE_REQUIREMENT_COUNT=' in installer) and count_line not in installer.splitlines():
                raise ValueError('installer native binding count differs')
        elif 'native-requirements.sha256' in names:
            raise ValueError('unexpected native runtime binding')
        manifest = {}
        for line in z.read('manifest.sha256').decode().splitlines():
            digest, rel = line.split('  ', 1)
            if rel in manifest or not allowed(profile, rel) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('invalid ownership manifest')
            manifest[rel] = digest
        if not manifest:
            raise ValueError('empty ownership manifest')
        validate_gallery_runtime(profile, manifest)
        peers = load_peer_history(profile)
        expected_peers = oem_coinstall.validate(profile['id'], manifest, peers) if peers else {}
        peer_support = {n: z.read(n) for n in names if n.startswith('peers/')}
        inspected_peers = oem_coinstall.inspect_members(profile['id'], manifest, peer_support)
        if inspected_peers != expected_peers:
            raise ValueError('peer support differs from configured history')
        script = z.read('META-INF/com/google/android/update-binary')
        peer_flag = b"HAS_PEERS='" + (b'1' if peer_support else b'0') + b"'"
        index_sha = sha(peer_support['peers/index.sha256']) if peer_support else ''
        if (peer_flag not in script.splitlines()
                or ("PEER_INDEX_SHA='"+index_sha+"'").encode() not in script.splitlines()):
            raise ValueError('installer peer support binding differs')
        policy_members = {n for n in names if n.startswith('policy/')}
        if profile.get('policy'):
            context = oem_runtime_policy.context_for_profile(profile)
            if context is not None:
                config = context.config
                bindings = oem_retention.oem_ota_compat.hashes(z.read('policy/inputs.sha256'))
                if bindings != {p: config['inputs'][p] for p in config['runtime_inputs']}:
                    raise ValueError('API37 archive policy compiler/version context differs')
                if receipt_path in names and json.loads(z.read(receipt_path)).get(
                        'policy_base_context_sha256') != context.expected_sha256:
                    raise ValueError('API37 archive policy context receipt differs')
            expected = {'policy/' + p[len(oem_policy.STORAGE):]
                        for p in manifest if p.startswith(oem_policy.STORAGE)}
            if policy_members != expected:
                raise ValueError('incomplete policy rollback support')
            for name in policy_members:
                if sha(z.read(name)) != manifest[oem_policy.STORAGE + name[len('policy/'):]]:
                    raise ValueError('policy support hash mismatch')
            patch_rows = z.read('policy/patches.tsv').decode().splitlines()
            if len(patch_rows) != 4 or {r.split()[-1] for r in patch_rows} != oem_policy.PATCH_PATHS:
                raise ValueError('incomplete or duplicate policy transaction')
            for row in patch_rows:
                after, before, path = row.split()
                if (path not in oem_policy.PATCH_PATHS or manifest.get(path) != after
                        or sha(z.read('policy/preimages/' + PurePosixPath(path).name)) != before
                        or sha(z.read('policy/postimages/' + PurePosixPath(path).name)) != after):
                    raise ValueError('invalid policy transaction binding')
        elif policy_members:
            raise ValueError('unexpected policy support')
        for name in names:
            if name.startswith('payload/'):
                rel = name[len('payload/'):]
                if manifest.get(rel) != sha(z.read(name)):
                    raise ValueError('payload hash mismatch')
        if retention:
            support = {oem_policy.STORAGE + n[len('policy/'):]: z.read(n) for n in policy_members}
            descriptor = oem_retention.descriptor(profile, manifest, support)
            expected_hook = oem_retention.render(descriptor)
            if manifest.get('system/addon.d/' + profile['hook']) != sha(expected_hook):
                raise ValueError('retention hook differs from archive contract')
            if any(n.startswith('payload/') for n in names):
                receipt = json.loads(z.read('payload/product/etc/realme-oem-addons/' + profile['id'] + '.json'))
                if (receipt.get('ota_retention') != retention
                        or receipt.get('platform_framework_sha256') != profile['platform_framework_sha256']):
                    raise ValueError('retention receipt differs from profile')
        script = z.read('META-INF/com/google/android/update-binary')
        expected_flag = b"HAS_POLICY='" + (b'1' if profile.get('policy') else b'0') + b"'"
        if expected_flag not in script.splitlines():
            raise ValueError('installer policy profile mismatch')
        upgrade_members = {n for n in names if n.startswith('upgrades/')}
        upgrade_flag = b"HAS_UPGRADES='" + (b'1' if upgrade_members else b'0') + b"'"
        if upgrade_flag not in script.splitlines():
            raise ValueError('installer predecessor flag mismatch')
        if upgrade_members:
            if profile['id'] not in ('camera', 'photos') or 'upgrades/index.sha256' not in upgrade_members:
                raise ValueError('unexpected predecessor profile/index')
            expected_upgrades = {'upgrades/index.sha256'}
            predecessors = {}
            for line in z.read('upgrades/index.sha256').decode().splitlines():
                digest, version = line.split('  ', 1)
                if version in predecessors or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}', version):
                    raise ValueError('invalid predecessor index')
                member = 'upgrades/' + version + '.sha256'
                data = z.read(member)
                if sha(data) != digest:
                    raise ValueError('predecessor manifest hash mismatch')
                previous = {}
                for row in data.decode().splitlines():
                    value, relative = row.split('  ', 1)
                    if relative in previous:
                        raise ValueError('duplicate predecessor file')
                    previous[relative] = value
                predecessors[version] = previous
                expected_upgrades.add(member)
                space_member = 'upgrades/' + version + '.space'
                space = z.read(space_member).decode()
                if not re.fullmatch(r'[0-9]{1,8} [0-9]{1,8}\n', space):
                    raise ValueError('invalid predecessor space budget')
                system_kib, product_kib = map(int, space.split())
                if system_kib < 4096 or product_kib < 16384:
                    raise ValueError('predecessor space reserve missing')
                if any(n.startswith('payload/') for n in names):
                    budget = install_budget({n[8:]: z.read(n) for n in names
                                             if n.startswith('payload/')}, previous)
                    if (system_kib, product_kib) != (budget['system'], budget['product']):
                        raise ValueError('predecessor space budget does not match payload delta')
                expected_upgrades.add(space_member)
            if upgrade_members != expected_upgrades:
                raise ValueError('unindexed predecessor metadata')
            # Hash-only preimages suffice to independently check common policy roots.
            policy_files = {oem_policy.STORAGE + n[len('policy/'):]: z.read(n)
                            for n in policy_members}
            checked_upgrades(profile, policy_files, predecessors)
        subprocess.run(['sh', '-n'], input=script, check=True, capture_output=True)
        has_payload = any(n.startswith('payload/') for n in names)
        action = re.search(rb"^ACTION='(install|remove|check)'$", script, re.M)
        if not action or has_payload != (action.group(1) == b'install'):
            raise ValueError('operation/payload mismatch')
        if has_payload and {n[8:] for n in names if n.startswith('payload/')} != set(manifest):
            raise ValueError('incomplete payload')
        if has_payload:
            xml = ET.fromstring(z.read('payload/product/etc/sysconfig/realme-' + profile['id'] + '.xml'))
            if (xml.tag != 'config' or len(xml) != 1
                    or xml[0].tag != 'hidden-api-whitelisted-app'
                    or xml[0].attrib != {'package': profile['package']}):
                raise ValueError('profile policy is not isolated to its selected app')
            for path in profile.get('shared_paths', ()):
                if (profile.get('policy',{}).get('kind') == oem_runtime_policy.KIND
                        and path in oem_runtime_policy.shared_paths()):
                    # Already checked by the complete policy transaction above;
                    # signer-policy XML is not a Java SDK registration XML.
                    continue
                if not path.endswith('.xml'):
                    continue
                library = ET.fromstring(z.read('payload/' + path))
                if (library.tag != 'permissions' or len(library) != 1
                        or library[0].tag != 'library'
                        or library[0].attrib != {'name': 'com.oplus.camera.unit.sdk',
                                                'file': '/' + next(p for p in profile['shared_paths']
                                                                  if p.endswith('.jar'))}):
                    raise ValueError('unexpected shared SDK registration or permissions')
        return {'owned_files': len(manifest), 'payload_present': has_payload,
                'manifest_sha256': sha(z.read('manifest.sha256'))}


def build(args):
    from verify_signapk_ota import signer_cert
    config_path = getattr(args, 'config', None) or CONFIG
    config = json.loads(config_path.read_text())
    config_platform(config)
    selected = args.profile or list(config['profiles'])
    shared = config.get('shared_sdk')
    if shared and any(name not in shared.get('compatible_profiles', ('camera', 'photos'))
                      for name in selected):
        raise ValueError('shared SDK is Camera-only; select --profile camera or prepare '
                         'a separately reviewed SDK for Photos')
    android = args.android_tree
    java = android / 'prebuilts/jdk/jdk21/linux-x86/bin/java'
    signer_jar = android / 'prebuilts/sdk/tools/lib/signapk.jar'
    apk_signer = android / 'prebuilts/sdk/tools/linux/lib/apksigner.jar'
    aapt = android / 'prebuilts/sdk/tools/linux/bin/aapt2'
    cert = (args.signing_dir / 'releasekey.x509.pem').read_bytes()
    project_cert = (ROOT / 'certs/rmx3301-releasekey.x509.pem').read_bytes()
    if ssl.PEM_cert_to_DER_cert(cert.decode()) != ssl.PEM_cert_to_DER_cert(project_cert.decode()):
        raise ValueError('release signer differs from project certificate')
    args.out.mkdir(parents=True, exist_ok=True)
    report = {'schema': 1, 'status': 'offline candidate, runtime unvalidated', 'profiles': {},
              'config_sha256': sha(config_path.read_bytes()),
              'installer_sha256': sha(installer_template(config))}
    oem_platform.carry(report, config)
    for name in selected:
        profile = checked_profile(config, name)
        shared = config.get('shared_sdk')
        if shared:
            profile['shared_paths'] = [shared['jar_path'], shared['xml_path']]
            profile['shared_paths'] += [s['path'] for s in profile.get('platform_native', ())]
            if profile.get('policy',{}).get('kind') == oem_runtime_policy.KIND:
                profile['shared_paths'] += sorted(oem_runtime_policy.shared_paths())
        source = args.source_root / profile['source']
        apk = source.read_bytes()
        if sha(apk) != profile['sha256']:
            raise ValueError('pinned stock APK mismatch: ' + name)
        checked = subprocess.check_output([str(java), '-Xmx256m', '-jar', str(apk_signer),
                                           'verify', '--print-certs', str(source)], text=True)
        if 'SHA-256 digest: ' + profile['certificate_sha256'] not in checked:
            raise ValueError('stock APK signing identity mismatch')
        badging = subprocess.check_output([str(aapt), 'dump', 'badging', str(source)], text=True)
        if "package: name='" + profile['package'] + "'" not in badging:
            raise ValueError('stock APK package mismatch')
        if "versionName='" + profile['version'] + "'" not in badging:
            raise ValueError('stock APK version mismatch')
        shared_input = (shared, (args.source_root / shared['source']).read_bytes()) if shared else None
        native_inputs = []
        for spec in profile.get('extra_native', ()):
            safe_path(spec['source'])
            native_inputs.append((spec, (args.source_root / spec['source']).read_bytes()))
        policy_input = None
        if profile.get('policy'):
            spec = profile['policy']
            safe_path(spec['source'])
            loader = oem_runtime_policy if spec.get('kind') == oem_runtime_policy.KIND else oem_policy
            if oem_platform.checked(profile) == oem_platform.ANDROID17:
                if loader is not oem_runtime_policy:
                    raise ValueError('API37 requires the shared policy context')
                policy_input = loader.load_bundle(args.source_root / spec['source'], spec['sha256'],
                    context=oem_runtime_policy.context_for_profile(profile))
            else:
                oem_runtime_policy.context_for_profile(profile)
                policy_input = loader.load_bundle(args.source_root / spec['source'], spec['sha256'])
        platform_inputs = []
        for spec in profile.get('platform_native', ()):
            safe_path(spec['source'])
            platform_inputs.append((spec, (args.source_root / spec['source']).read_bytes()))
        asset_inputs = []
        for spec in profile.get('extra_assets', ()):
            safe_path(spec['source'])
            asset_inputs.append((spec, (args.source_root / spec['source']).read_bytes()))
        files = payload(profile, apk, shared_input, native_inputs, policy_input, platform_inputs,
                        asset_inputs)
        upgrade_spec = profile.get('upgrade_history')
        upgrades = {}
        if upgrade_spec:
            history_path = ROOT / safe_path(upgrade_spec['source'])
            if (not history_path.resolve().is_relative_to((ROOT / 'configs').resolve())
                    or sha(history_path.read_bytes()) != upgrade_spec['sha256']):
                raise ValueError('unreviewed predecessor history')
            history = json.loads(history_path.read_text())
            upgrades = checked_upgrade_history(profile, files, history)
        rows = []
        for action in ('install', 'remove', 'check'):
            stem = 'realme-' + name + '-' + oem_platform.checked(profile)['lineage'] + '-' + config['release'] + '-' + action
            output = args.out / (stem + '.zip')
            if output.exists():
                raise ValueError('refusing to overwrite an existing build')
            with tempfile.TemporaryDirectory(prefix='oem-package-', dir=args.scratch) as tmp:
                scratch = Path(tmp)
                unsigned, signed = scratch / 'unsigned.zip', scratch / 'signed.zip'
                helper = ((args.source_root / profile['recovery_helper']['source']).read_bytes()
                          if profile.get('recovery_helper') else None)
                unsigned.write_bytes(archive_bytes(profile, files, action, cert, upgrades, helper))
                inspected = inspect_archive(unsigned, profile)
                subprocess.run([str(java), '-Xmx768m', '-Djava.library.path=' +
                                str(android / 'prebuilts/sdk/tools/linux/lib64'), '-jar', str(signer_jar),
                                '-w', str(args.signing_dir / 'releasekey.x509.pem'),
                                str(args.signing_dir / 'releasekey.pk8'), str(unsigned), str(signed)],
                               check=True, capture_output=True)
                with zipfile.ZipFile(signed) as z:
                    signature = signer_cert(signed, z, scratch, 'openssl')
                expected = sha(ssl.PEM_cert_to_DER_cert(project_cert.decode()))
                if signature['cms_signer_sha256'] != expected or not signature['signer_matches_otacert']:
                    raise ValueError('recovery ZIP signing verification failed')
                inspect_archive(signed, profile)
                data = signed.read_bytes()
                with output.open('xb') as collected:
                    collected.write(data)
                if sha(output.read_bytes()) != sha(data):
                    raise ValueError('collected package hash mismatch')
                rows.append(dict(action=action, path=str(output), bytes=len(data),
                                 sha256=sha(data), inspection=inspected, signature=signature))
        report['profiles'][name] = dict(package=profile['package'], apk_sha256=profile['sha256'],
                                        payload_bytes=sum(map(len, files.values())), artifacts=rows)
    (args.out / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--android-tree', type=Path, required=True)
    parser.add_argument('--signing-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--profile', choices=('camera', 'photos'), action='append')
    parser.add_argument('--config', type=Path, default=CONFIG)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    result = build(args)
    print(json.dumps({'status': result['status'], 'profiles': list(result['profiles'])}))


if __name__ == '__main__':
    main()
