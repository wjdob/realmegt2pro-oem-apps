"""Prepare a shared, reversible policy for the two optional OEM app profiles.

This is offline packaging. Exact APK signers and package names select separate
vendor-client domains; the ordinary untrusted-app domain is never widened.
The legacy Camera-only bundle remains immutable and supplies the pinned preimages.
"""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import oem_policy

ROOT = Path(__file__).resolve().parents[1]
KIND = 'shared-oem-runtime-v1'
STORAGE = oem_policy.STORAGE
PATCH_PATHS = oem_policy.PATCH_PATHS
GALLERY_FRAGMENT = ROOT/'tools/oem_gallery_compat/gallery-policy.cil'
GALLERY_FRAGMENT_SHA = '8e2ddcfb510d6adbec2cbcbb0c6c469bf4249ecf6c71c8704c5d3b5e1e6e92f0'
GALLERY_PACKAGE = 'com.coloros.gallery3d'
GALLERY_CERT_SHA = 'bd77b13eed39c33c246bd3340a2049dc35487fec6855191b74ac606fba730ada'
CAMERA_PACKAGE = 'com.oplus.camera'
CAMERA_CERT_SHA = 'b0a9bbfc05eee5e7d0a2c97c030586e15bb3301152078f54473bb82df6d8c818'
EDITOR_BACKEND_PATH = 'odm/lib64/libAlgoProcess.so'
EDITOR_BACKEND_SHA = '726183ac2d9073ca66497cfae074e421887d81298c0fb01532b7201d01647d30'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def support_paths():
    return frozenset({STORAGE+'inputs.sha256', STORAGE+'patches.tsv'} |
        {STORAGE+side+'/'+Path(p).name for side in ('preimages','postimages')
         for p in PATCH_PATHS})


def shared_paths():
    return PATCH_PATHS | support_paths()


def certificate_map(camera_der, gallery_der):
    if sha(camera_der) != CAMERA_CERT_SHA or sha(gallery_der) != GALLERY_CERT_SHA:
        raise ValueError('unreviewed optional-app signer')
    root = ET.Element('policy')
    for der, package, seinfo in ((camera_der, CAMERA_PACKAGE, 'realme_camera'),
                                 (gallery_der, GALLERY_PACKAGE, 'realme_gallery')):
        signer = ET.SubElement(root, 'signer', signature=der.hex())
        entry = ET.SubElement(signer, 'package', name=package)
        ET.SubElement(entry, 'seinfo', value=seinfo)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)+b'\n'


def desired_files(preimages, camera_der, gallery_der, mapping):
    config = json.loads(oem_policy.CONFIG.read_text())
    if set(preimages) != PATCH_PATHS or any(sha(data) != config['inputs'][p]
                                          for p,data in preimages.items()):
        raise ValueError('unreviewed original policy preimages')
    fragment = GALLERY_FRAGMENT.read_bytes()
    camera = oem_policy.FRAGMENT.read_bytes()
    if (sha(fragment) != GALLERY_FRAGMENT_SHA or
            sha(camera) != config['fragment_sha256'] or
            sha(mapping) != config['inputs']['product/etc/selinux/mapping/202504.cil']):
        raise ValueError('policy recipe or mapping differs')
    prefix = 'product/etc/selinux/'
    cil = preimages[prefix+'product_sepolicy.cil']+b'\n'+camera+b'\n'+fragment
    seapp = preimages[prefix+'product_seapp_contexts']
    for package, label in ((CAMERA_PACKAGE,'realme_camera'),
                           (GALLERY_PACKAGE,'realme_gallery')):
        if package.encode() in seapp or label.encode() in seapp:
            raise ValueError('duplicate app mapping in original policy')
        seapp += ('\nuser=_app seinfo='+label+' name='+package+' domain='+label+
                  '_app type=app_data_file levelFrom=all\n').encode()
    return {prefix+'product_sepolicy.cil':cil,
            prefix+'product_seapp_contexts':seapp,
            prefix+'product_mac_permissions.xml':certificate_map(camera_der,gallery_der),
            prefix+'product_sepolicy_and_mapping.sha256':sha(cil+mapping).encode()+b'\n'}


def compile_proof(report, cil):
    if (not report.get('compile_passed') or not report.get('live_policy_unchanged')
            or report.get('candidate_sha256') != sha(cil)):
        raise ValueError('matching compile-only proof required')
    config = json.loads(oem_policy.CONFIG.read_text())
    rows = [r for r in report['commands'] if r['args'][:2] == ['shell','/system/bin/secilc']]
    if len(rows) != 1 or rows[0]['code'] != 0 or '-N' in rows[0]['args']:
        raise ValueError('neverallow-enabled compilation required')
    expected = {'/'+p for p in config['compiler_inputs']
                if p != 'product/etc/selinux/product_sepolicy.cil'}
    if not expected <= set(rows[0]['args']):
        raise ValueError('policy compiler inputs incomplete')
    if any(report.get('input_hashes',{}).get(p) != config['inputs'][p]
           for p in config['compiler_inputs'] if p != 'product/etc/selinux/product_sepolicy.cil'):
        raise ValueError('policy compiler context differs from pinned baseline')


def prepare(legacy_root, legacy_sha, gallery_der, mapping, compilation, output):
    legacy_manifest, legacy = oem_policy.load_bundle(legacy_root,legacy_sha)
    pre = {p:legacy[STORAGE+'preimages/'+Path(p).name] for p in PATCH_PATHS}
    camera_mac = ET.fromstring(legacy['product/etc/selinux/product_mac_permissions.xml'])
    camera_der = bytes.fromhex(camera_mac[0].attrib['signature'])
    desired = desired_files(pre,camera_der,gallery_der,mapping)
    compile_proof(compilation,desired['product/etc/selinux/product_sepolicy.cil'])
    patches = [dict(path=p,before=sha(pre[p]),after=sha(desired[p]),
                    preimage=STORAGE+'preimages/'+Path(p).name,
                    postimage=STORAGE+'postimages/'+Path(p).name) for p in sorted(PATCH_PATHS)]
    files = {}
    for row in patches:
        files[row['preimage']] = pre[row['path']]
        files[row['postimage']] = desired[row['path']]
    files[STORAGE+'patches.tsv'] = ''.join(r['after']+' '+r['before']+' '+r['path']+'\n'
                                          for r in patches).encode()
    files[STORAGE+'inputs.sha256'] = legacy[STORAGE+'inputs.sha256']
    manifest = dict(schema=1,kind=KIND,legacy_manifest_sha256=legacy_sha,
        base_config_sha256=sha(oem_policy.CONFIG.read_bytes()),
        camera_fragment_sha256=legacy_manifest['fragment_sha256'],
        gallery_fragment_sha256=GALLERY_FRAGMENT_SHA,
        packages={CAMERA_PACKAGE:CAMERA_CERT_SHA,GALLERY_PACKAGE:GALLERY_CERT_SHA},
        mapping_sha256=sha(mapping),patches=patches,
        files={p:sha(v) for p,v in sorted(files.items())},
        compile_report_sha256=sha(json.dumps(compilation,sort_keys=True).encode()),
        scope='Separate certificate-and-package-bound vendor-client app domains; no permissive or global untrusted-app change. Shared by recognized optional Camera/Gallery bundles and restored when the last owner is removed.')
    if output.exists():
        raise ValueError('refusing to overwrite policy bundle')
    output.mkdir(parents=True)
    for p,data in files.items():
        path=output/p;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (output/'mapping.cil').write_bytes(mapping)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def load_bundle(root, manifest_sha):
    raw = oem_policy.checked_file(root,'manifest.json')
    if sha(raw) != manifest_sha:
        raise ValueError('shared policy manifest hash differs')
    m = json.loads(raw)
    if (m['schema'] != 1 or m['kind'] != KIND or
            m['base_config_sha256'] != sha(oem_policy.CONFIG.read_bytes()) or
            m['gallery_fragment_sha256'] != GALLERY_FRAGMENT_SHA or
            m['camera_fragment_sha256'] != sha(oem_policy.FRAGMENT.read_bytes()) or
            m['packages'] != {CAMERA_PACKAGE:CAMERA_CERT_SHA,GALLERY_PACKAGE:GALLERY_CERT_SHA} or
            set(m['files']) != support_paths()):
        raise ValueError('shared policy identity or scope differs')
    files = {p:oem_policy.checked_file(root,p) for p in support_paths()}
    if any(sha(files[p]) != digest for p,digest in m['files'].items()):
        raise ValueError('shared policy support hash differs')
    pre = {p:files[STORAGE+'preimages/'+Path(p).name] for p in PATCH_PATHS}
    mac = ET.fromstring(files[STORAGE+'postimages/product_mac_permissions.xml'])
    if len(mac) != 2:
        raise ValueError('unexpected signer mappings')
    desired = desired_files(pre,bytes.fromhex(mac[0].attrib['signature']),
                            bytes.fromhex(mac[1].attrib['signature']),
                            oem_policy.checked_file(root,'mapping.cil'))
    expected_patches = []
    for p in sorted(PATCH_PATHS):
        name = Path(p).name
        if files[STORAGE+'postimages/'+name] != desired[p]:
            raise ValueError('shared policy body exceeds reviewed recipe')
        expected_patches.append(dict(path=p,before=sha(pre[p]),after=sha(desired[p]),
            preimage=STORAGE+'preimages/'+name,postimage=STORAGE+'postimages/'+name))
        files[p] = desired[p]
    config = json.loads(oem_policy.CONFIG.read_text())
    expected_inputs = ''.join(config['inputs'][p]+'  '+p+'\n' for p in config['compiler_inputs']).encode()
    expected_tsv = ''.join(r['after']+' '+r['before']+' '+r['path']+'\n' for r in expected_patches).encode()
    if (m['patches'] != expected_patches or files[STORAGE+'inputs.sha256'] != expected_inputs or
            files[STORAGE+'patches.tsv'] != expected_tsv):
        raise ValueError('shared policy transaction differs')
    return m,files
