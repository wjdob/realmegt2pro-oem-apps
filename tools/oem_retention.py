"""Render a reviewed V3 retention-hook candidate, without installing or signing it.

This consumes verified oem_ota_compat bundle descriptors. Destination compatibility,
full APK signatures, recovery transactions and a real OTA remain separate gates.
"""
from pathlib import Path
import hashlib
import re

import oem_coinstall
import oem_ota_compat
import oem_runtime_policy
import oem_platform

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'tools/oem_addons/retention-v3.sh'
KIND = 'lineage-v3-reviewed-context-v1'
KIND37 = 'lineage-v3-reviewed-context-api37-v1'


def template_bytes(record):
    return oem_platform.template(TEMPLATE.read_bytes(), record, 'retention')


def expected_spec(record):
    kind = KIND if oem_platform.checked(record) == oem_platform.LEGACY else KIND37
    return {'kind': kind, 'template_sha256': hashlib.sha256(template_bytes(record)).hexdigest()}


def checked_spec(profile):
    spec = profile.get('ota_retention')
    if spec is None:
        return None
    if (not isinstance(spec, dict) or spec != expected_spec(profile)
            or profile.get('explicit_reinstall_after_ota')
            or profile.get('policy', {}).get('kind') != oem_runtime_policy.KIND
            or not profile.get('peer_history') or not profile.get('native_requirements')
            or not re.fullmatch('[0-9a-f]{64}', profile.get('platform_framework_sha256', ''))):
        raise ValueError('unreviewed or conflicting OTA retention contract')
    identity = (profile.get('package'), profile.get('certificate_sha256'))
    if identity != oem_ota_compat.PACKAGES.get(profile.get('id')):
        raise ValueError('retention app identity differs')
    return dict(spec)


def descriptor(profile, manifest, support):
    """Bind packager metadata to the same complete context as archive inspection."""
    if not checked_spec(profile):
        raise ValueError('explicit retention contract required')
    prefix = oem_ota_compat.STORAGE
    inputs = oem_ota_compat.hashes(support[prefix + 'inputs.sha256'])
    if set(inputs) != oem_ota_compat.policy_inputs_for(profile):
        raise ValueError('incomplete retention compiler context')
    patches = support[prefix + 'patches.tsv'].decode().splitlines()
    seen = set()
    for line in patches:
        fields = line.split()
        if len(fields) != 3:
            raise ValueError('invalid retention policy transaction')
        after, before, path = fields
        if (path not in oem_ota_compat.POLICY or path in seen
                or not re.fullmatch('[0-9a-f]{64}', before)
                or not re.fullmatch('[0-9a-f]{64}', after)
                or manifest.get(path) != after
                or manifest.get(prefix + 'preimages/' + Path(path).name) != before
                or manifest.get(prefix + 'postimages/' + Path(path).name) != after
                or (path in inputs and inputs[path] != before)):
            raise ValueError('retention policy binding differs')
        seen.add(path)
        inputs[path] = before
    if seen != oem_ota_compat.POLICY:
        raise ValueError('incomplete retention policy transaction')
    native = dict(profile['native_requirements'])
    native['system/framework/oplus-fwk.jar'] = profile['platform_framework_sha256']
    for path, digest in native.items():
        oem_ota_compat.safe_path(path)
        if (not re.fullmatch('[0-9a-f]{64}', digest)
                or (path in inputs and inputs[path] != digest)):
            raise ValueError('conflicting retention dependency')
        inputs[path] = digest
    apks = [path for path in manifest if path.endswith('/base.apk')]
    if len(apks) != 1 or manifest[apks[0]] != profile['sha256']:
        raise ValueError('retention APK differs from profile')
    return oem_platform.carry({'profile': profile['id'], 'package': profile['package'],
            'receipt_signer_sha256': profile['certificate_sha256'],
            'manifest': dict(manifest), 'target_bindings': inputs}, profile)


def render(bundle):
    return _render(bundle, require_hook=True)


def render_unhooked(bundle):
    """For payload construction, before the generated hook has an own digest."""
    return _render(bundle, require_hook=False)


def _render(bundle, require_hook):
    profile = bundle['profile']
    if profile not in oem_ota_compat.PACKAGES:
        raise ValueError('unsupported retention profile')
    package, certificate = oem_ota_compat.PACKAGES[profile]
    if (bundle.get('package') != package or bundle.get('receipt_signer_sha256') != certificate):
        raise ValueError('retention app identity differs')
    files = dict(bundle['manifest'])
    hook = oem_coinstall.HOOKS[profile]
    if (hook in files) != require_hook or len(files) > (512 if require_hook else 511):
        raise ValueError('missing owned hook or excessive manifest')
    oem_coinstall.app_root(profile, files)
    for path, digest in files.items():
        if not oem_coinstall.allowed(profile, path) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('retention ownership exceeds profile')
    # backuptool preserves addon.d itself. Including this script's own digest in
    # its embedded manifest would be recursive; all other owned bytes are bound.
    files.pop(hook, None)
    bindings = bundle['target_bindings']
    required = oem_ota_compat.policy_inputs_for(bundle) | oem_ota_compat.POLICY
    if not required <= bindings.keys() or len(bindings) > 64:
        raise ValueError('incomplete retention compiler context')
    for path, digest in bindings.items():
        oem_ota_compat.safe_path(path)
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid retention binding')
    if not oem_ota_compat.POLICY <= files.keys():
        raise ValueError('incomplete retention policy transaction')
    values = {
        'PROFILE': profile,
        'TEMPLATE_SHA': hashlib.sha256(template_bytes(bundle)).hexdigest(),
        'FILES': ''.join(digest + '  ' + path + '\n' for path, digest in sorted(files.items())).rstrip(),
        'BINDINGS': ''.join(digest + '  ' + path + '\n' for path, digest in sorted(bindings.items())).rstrip(),
    }
    text = template_bytes(bundle).decode('utf-8')
    for key, value in values.items():
        text = text.replace('@' + key + '@', value)
    if re.search('@[A-Z_]+@', text):
        raise ValueError('unexpanded retention template')
    return text.encode()
