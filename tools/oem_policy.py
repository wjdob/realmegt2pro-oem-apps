"""Prepare exact, reversible optional Camera policy files; never change a device.

Only the pinned original Camera certificate/package receives its own vendor-client
domain. Original policy files and all compiler inputs are bound by full hashes.
The installed kernel policy is never patched or made permissive by this tool.
"""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/oem-camera-policy-23.2.json'
FRAGMENT = ROOT / 'tools/oem_camera_compat/camera-policy.cil'
STORAGE = 'product/etc/realme-oem-addons/camera-policy/'
PATCH_PATHS = frozenset('product/etc/selinux/' + name for name in (
    'product_mac_permissions.xml', 'product_seapp_contexts',
    'product_sepolicy.cil', 'product_sepolicy_and_mapping.sha256'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_file(root, relative):
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('policy source path escapes root')
    return path.read_bytes()


def prepare(original, stock_mac, compile_report, output):
    config = json.loads(CONFIG.read_text())
    if set(config['patch_paths']) != PATCH_PATHS:
        raise ValueError('unreviewed patch scope')
    if output.exists():
        raise ValueError('refusing to overwrite a policy bundle')
    inputs = {p: checked_file(original, p) for p in config['inputs']}
    if any(sha(inputs[p]) != digest for p, digest in config['inputs'].items()):
        raise ValueError('unreviewed original policy input')
    fragment = FRAGMENT.read_text().encode()
    if sha(fragment) != config['fragment_sha256']:
        raise ValueError('unreviewed Camera policy fragment')
    prefix = 'product/etc/selinux/'
    original_cil = inputs[prefix + 'product_sepolicy.cil']
    mapping = inputs['product/etc/selinux/mapping/' + config['mapping_version'] + '.cil']
    if sha(original_cil + mapping).encode() + b'\n' != inputs[prefix + 'product_sepolicy_and_mapping.sha256']:
        raise ValueError('original policy/mapping digest does not match')
    patched_cil = original_cil + b'\n' + fragment
    if sha(patched_cil + mapping) != config['combined_policy_digest']:
        raise ValueError('reviewed combined policy digest changed')
    compilation = json.loads(compile_report.read_text())
    if (compilation.get('compile_passed') is not True
            or compilation.get('candidate_sha256') != sha(patched_cil)
            or compilation.get('fragment_sha256') != sha(fragment)):
        raise ValueError('matching successful policy compilation required')
    calls = [r for r in compilation['commands'] if r['args'][:2] == ['shell', '/system/bin/secilc']]
    if len(calls) != 1 or calls[0]['code'] != 0 or '-N' in calls[0]['args']:
        raise ValueError('neverallow checking must be enabled')
    expected = {'/' + p for p in config['compiler_inputs'] if p != prefix + 'product_sepolicy.cil'}
    if not expected <= set(calls[0]['args']):
        raise ValueError('incomplete compiler input context')

    stock = ET.fromstring(stock_mac.read_bytes())
    certificates = set()
    for signer in stock.findall('signer'):
        if any(p.attrib.get('name') == config['package'] for p in signer.findall('package')):
            cert = bytes.fromhex(signer.attrib['signature'])
            if sha(cert) == config['certificate_sha256']:
                certificates.add(cert.hex())
    if len(certificates) != 1:
        raise ValueError('unambiguous authentic Camera certificate required')
    mac = ET.fromstring(inputs[prefix + 'product_mac_permissions.xml'])
    # Current supported baseline has no product signers. Do not broaden/merge unknown trust.
    if mac.tag != 'policy' or len(mac):
        raise ValueError('unsupported product signer policy')
    signer = ET.SubElement(mac, 'signer', signature=certificates.pop())
    package = ET.SubElement(signer, 'package', name=config['package'])
    ET.SubElement(package, 'seinfo', value='realme_camera')
    mac_bytes = ET.tostring(mac, encoding='utf-8', xml_declaration=True) + b'\n'
    seapp = inputs[prefix + 'product_seapp_contexts']
    if config['package'].encode() in seapp or b'realme_camera' in seapp:
        raise ValueError('Camera seapp mapping already exists')
    seapp += (b'\nuser=_app seinfo=realme_camera name=com.oplus.camera '
              b'domain=realme_camera_app type=app_data_file levelFrom=all\n')
    patched = {
        prefix + 'product_mac_permissions.xml': mac_bytes,
        prefix + 'product_seapp_contexts': seapp,
        prefix + 'product_sepolicy.cil': patched_cil,
        prefix + 'product_sepolicy_and_mapping.sha256': sha(patched_cil + mapping).encode() + b'\n',
    }
    files = {}
    patches = []
    for path in sorted(PATCH_PATHS):
        name = Path(path).name
        before = STORAGE + 'preimages/' + name
        after = STORAGE + 'postimages/' + name
        files[before] = inputs[path]
        files[after] = patched[path]
        patches.append(dict(path=path, preimage=before, postimage=after,
                            before=sha(inputs[path]), after=sha(patched[path])))
    files[STORAGE + 'patches.tsv'] = ''.join(
        r['after'] + ' ' + r['before'] + ' ' + r['path'] + '\n' for r in patches).encode()
    files[STORAGE + 'inputs.sha256'] = ''.join(
        config['inputs'][p] + '  ' + p + '\n' for p in config['compiler_inputs']).encode()
    manifest = dict(schema=1, package=config['package'], certificate_sha256=config['certificate_sha256'],
                    config_sha256=sha(CONFIG.read_bytes()), fragment_sha256=sha(fragment),
                    patches=patches, files={p: sha(data) for p, data in sorted(files.items())},
                    scope='Exact optional Camera-only mapping and vendor-client app domain; '
                          'no permissive domain, global app access grant or firmware write.')
    output.mkdir(parents=True)
    for path, data in files.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def load_bundle(root, manifest_sha):
    manifest_bytes = checked_file(root, 'manifest.json')
    if sha(manifest_bytes) != manifest_sha:
        raise ValueError('policy manifest hash mismatch')
    manifest = json.loads(manifest_bytes)
    config = json.loads(CONFIG.read_text())
    fragment = FRAGMENT.read_text().encode()
    if (manifest['config_sha256'] != sha(CONFIG.read_bytes())
            or manifest['package'] != config['package']
            or manifest['certificate_sha256'] != config['certificate_sha256']
            or manifest['fragment_sha256'] != sha(fragment)
            or sha(fragment) != config['fragment_sha256']):
        raise ValueError('policy recipe binding changed')
    if {r['path'] for r in manifest['patches']} != PATCH_PATHS or len(manifest['patches']) != 4:
        raise ValueError('policy patch scope mismatch')
    expected = {STORAGE + 'patches.tsv', STORAGE + 'inputs.sha256'}
    expected |= {STORAGE + side + '/' + Path(p).name
                 for p in PATCH_PATHS for side in ('preimages', 'postimages')}
    if set(manifest['files']) != expected:
        raise ValueError('policy bundle file scope mismatch')
    files = {p: checked_file(root, p) for p in expected}
    if any(sha(files[p]) != digest for p, digest in manifest['files'].items()):
        raise ValueError('policy bundle input hash mismatch')
    for patch in manifest['patches']:
        name = Path(patch['path']).name
        if (patch['preimage'] != STORAGE + 'preimages/' + name
                or patch['postimage'] != STORAGE + 'postimages/' + name
                or sha(files[patch['preimage']]) != patch['before']
                or patch['before'] != config['inputs'][patch['path']]
                or sha(files[patch['postimage']]) != patch['after']):
            raise ValueError('policy pre/postimage binding mismatch')
        files[patch['path']] = files[patch['postimage']]
    prefix = 'product/etc/selinux/'
    cil = prefix + 'product_sepolicy.cil'
    before_cil = files[STORAGE + 'preimages/product_sepolicy.cil']
    if files[cil] != before_cil + b'\n' + fragment:
        raise ValueError('policy body differs from reviewed fragment')
    if (files[prefix + 'product_sepolicy_and_mapping.sha256']
            != config['combined_policy_digest'].encode() + b'\n'):
        raise ValueError('policy compiler cache digest differs from recipe')
    contexts = prefix + 'product_seapp_contexts'
    before_contexts = files[STORAGE + 'preimages/product_seapp_contexts']
    expected_contexts = (before_contexts + b'\nuser=_app seinfo=realme_camera '
                         b'name=com.oplus.camera domain=realme_camera_app '
                         b'type=app_data_file levelFrom=all\n')
    if files[contexts] != expected_contexts:
        raise ValueError('policy context mapping exceeds Camera scope')
    mac = ET.fromstring(files[prefix + 'product_mac_permissions.xml'])
    if (mac.tag != 'policy' or len(mac) != 1 or mac[0].tag != 'signer'
            or set(mac[0].attrib) != {'signature'} or len(mac[0]) != 1
            or sha(bytes.fromhex(mac[0].attrib['signature'])) != config['certificate_sha256']
            or mac[0][0].tag != 'package' or mac[0][0].attrib != {'name': config['package']}
            or len(mac[0][0]) != 1 or mac[0][0][0].tag != 'seinfo'
            or mac[0][0][0].attrib != {'value': 'realme_camera'}
            or len(mac[0][0][0])):
        raise ValueError('signer policy is not restricted to the pinned Camera package')
    patches_tsv = ''.join(r['after'] + ' ' + r['before'] + ' ' + r['path'] + '\n'
                          for r in sorted(manifest['patches'], key=lambda r: r['path'])).encode()
    inputs_manifest = ''.join(config['inputs'][p] + '  ' + p + '\n'
                              for p in config['compiler_inputs']).encode()
    if (files[STORAGE + 'patches.tsv'] != patches_tsv
            or files[STORAGE + 'inputs.sha256'] != inputs_manifest):
        raise ValueError('policy transaction metadata differs from recipe')
    return manifest, files


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-root', type=Path, required=True)
    parser.add_argument('--stock-mac', type=Path, required=True)
    parser.add_argument('--compile-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.original_root, args.stock_mac, args.compile_report, args.output)
    print(json.dumps(dict(patches=len(result['patches']), support_files=len(result['files']),
                          scope=result['scope'])))
