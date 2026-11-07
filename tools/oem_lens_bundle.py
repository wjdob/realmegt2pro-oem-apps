"""Repackage pinned signed Camera/Photos bundles with the reviewed Lens SDK.

Only the shared JAR, its receipt hash and each app's retention hook may change.
Original APKs, signatures, native libraries, policy and permissions stay byte exact.
The caller supplies private release archives and the existing recovery signing key.
"""
from pathlib import Path
import argparse
import json
import subprocess
import tempfile
import zipfile
import re
import ssl

import oem_addons as addons
import oem_coinstall as coinstall
import oem_retention as retention
import oem_runtime_policy as policy
from verify_signapk_ota import signer_cert

ROOT = Path(__file__).resolve().parents[1]
SDK = 'product/framework/realme-camera-unit-sdk.jar'


def profile(config, name):
    item = (addons.checked_profile(config, name) if hasattr(addons,'checked_profile')
            else dict(config['profiles'][name],id=name,addon_release=config['release']))
    shared = config['shared_sdk']
    item['shared_paths'] = [shared['jar_path'], shared['xml_path']]
    item['shared_paths'] += [s['path'] for s in item.get('platform_native', ())]
    item['shared_paths'] += sorted(policy.shared_paths())
    return item


def read_source(archive, config, name, expected, scratch):
    if addons.sha(archive.read_bytes()) != expected:
        raise ValueError('source release archive differs')
    item = profile(config, name)
    addons.inspect_archive(archive, item)
    with zipfile.ZipFile(archive) as z:
        signature = signer_cert(archive, z, scratch, 'openssl')
        expected_cert = addons.sha(ssl.PEM_cert_to_DER_cert(
            (ROOT/'certs/rmx3301-releasekey.x509.pem').read_text()))
        if signature['cms_signer_sha256'] != expected_cert or not signature['signer_matches_otacert']:
            raise ValueError('source package recovery signer differs')
        files = {n[8:]: z.read(n) for n in z.namelist() if n.startswith('payload/')}
        helper = z.read('recovery/prepare-logical')
        manifest = dict(line.split('  ', 1)[::-1] for line in z.read('manifest.sha256').decode().splitlines())
        if {p: addons.sha(data) for p, data in files.items()} != manifest:
            raise ValueError('source payload content differs')
    return files, helper


def patch_payload(item, original, sdk):
    if addons.sha(sdk) != item['lens_sdk_sha256']:
        raise ValueError('Lens SDK differs from reviewed configuration')
    result = dict(original)
    result[SDK] = sdk
    receipt_path = 'product/etc/realme-oem-addons/'+item['id']+'.json'
    receipt = json.loads(result[receipt_path])
    if receipt['shared_sdk_sha256'] != addons.sha(original[SDK]):
        raise ValueError('source receipt SDK differs')
    receipt['shared_sdk_sha256'] = addons.sha(sdk)
    result[receipt_path] = (json.dumps(receipt, sort_keys=True, indent=2)+'\n').encode()
    hook = coinstall.HOOKS[item['id']]
    result.pop(hook)
    result[hook] = addons.hook(item, result)
    changed = {p for p in result if result[p] != original[p]}
    if changed != {SDK, receipt_path, hook} or result.keys() != original.keys():
        raise ValueError('Lens patch exceeds its three-file scope')
    addons.validate_gallery_runtime(item, {p:addons.sha(v) for p,v in result.items()})
    return result


def sign(unsigned, output, android, signing, scratch):
    java = android/'prebuilts/jdk/jdk21/linux-x86/bin/java'
    signer = android/'prebuilts/sdk/tools/lib/signapk.jar'
    cert = signing/'releasekey.x509.pem'
    if ssl.PEM_cert_to_DER_cert(cert.read_text()) != ssl.PEM_cert_to_DER_cert((ROOT/'certs/rmx3301-releasekey.x509.pem').read_text()):
        raise ValueError('recovery signing identity differs')
    subprocess.run([str(java), '-Xmx768m', '-Djava.library.path='+str(android/'prebuilts/sdk/tools/linux/lib64'),
        '-jar', str(signer), '-w', str(cert), str(signing/'releasekey.pk8'), str(unsigned), str(output)],
        check=True, capture_output=True)
    with zipfile.ZipFile(output) as z:
        proof = signer_cert(output,z,scratch,'openssl')
    expected_cert = addons.sha(ssl.PEM_cert_to_DER_cert(cert.read_text()))
    if proof['cms_signer_sha256'] != expected_cert or not proof['signer_matches_otacert']:
        raise ValueError('output recovery signature differs')
    return proof


def coordinated(children, scratch_kib):
    """Keep recognition and sequencing separate from the existing child engine."""
    expected = {'legacy-camera-check','legacy-photos-check','legacy-photos-remove',
                'legacy-photos-install','camera-check','camera-install','photos-check','photos-install'}
    if set(children) != expected or not 32768 <= scratch_kib < 2**31:
        raise ValueError('incomplete coordinated update or invalid space budget')
    index = ''.join(addons.sha(data)+'  packages/'+name+'.zip\n'
                    for name,data in sorted(children.items())).encode()
    script = (ROOT/'tools/oem_addons/lens-upgrade.sh').read_bytes()
    for key,value in {'INDEX_SHA':addons.sha(index), 'SCRATCH_KIB':str(scratch_kib),
                      'CHILD_CASE':'|'.join('packages/'+name+'.zip' for name in sorted(children))}.items():
        script=script.replace(('@'+key+'@').encode(),value.encode())
    if re.search(rb'@[A-Z_]+@',script):raise ValueError('unresolved coordinated template')
    subprocess.run(['sh','-n'],input=script,check=True,capture_output=True)
    import io
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as z:
        for name,data in sorted({'children.sha256':index,
            'META-INF/com/google/android/update-binary':script,
            'META-INF/com/google/android/updater-script':b'# Camera Lens coordinated updater\n',
            **{'packages/'+n+'.zip':v for n,v in children.items()}}.items()):
            info=zipfile.ZipInfo(name,(2009,1,1,0,0,0));info.external_attr=0o100644<<16
            z.writestr(info,data)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('config','camera','photos','sdk','android-tree','signing-dir','out','scratch'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    config=json.loads(args.config.read_text());spec=config['lens_patch']
    sdk=args.sdk.read_bytes()
    contract=json.loads((ROOT/spec['sdk_contract']['source']).read_text())
    if (addons.sha((ROOT/spec['sdk_contract']['source']).read_bytes()) != spec['sdk_contract']['sha256']
            or addons.sha(sdk) != contract['output_sdk_sha256']):
        raise ValueError('unreviewed SDK contract or SDK')
    args.out.mkdir(parents=True,exist_ok=False);args.scratch.mkdir(parents=True,exist_ok=True)
    cert=(ROOT/'certs/rmx3301-releasekey.x509.pem').read_bytes();report={'profiles':{},'status':'host-verified; new package runtime pending'}
    children={};max_payload=0
    for name in ('camera','photos'):
        old_config=json.loads((ROOT/spec['sources'][name]['config']).read_text())
        item=profile(config,name)
        with tempfile.TemporaryDirectory(dir=args.scratch) as temporary:
            scratch=Path(temporary)
            original,helper=read_source(getattr(args,name),old_config,name,spec['sources'][name]['sha256'],scratch)
            old_item=profile(old_config,name)
            children['legacy-'+name+'-check']=addons.archive_bytes(old_item,original,'check',cert,None,helper)
            if name=='photos':
                children['legacy-photos-remove']=addons.archive_bytes(old_item,original,'remove',cert,None,helper)
                children['legacy-photos-install']=args.photos.read_bytes()
            files=patch_payload(item,original,sdk)
            max_payload=max(max_payload,sum(map(len,files.values())),sum(map(len,original.values())))
            history=json.loads((ROOT/item['upgrade_history']['source']).read_text())
            upgrades=addons.checked_upgrade_history(item,files,history)
            rows=[]
            for action in ('check','remove','install'):
                unsigned=scratch/(action+'.zip')
                unsigned.write_bytes(addons.archive_bytes(item,files,action,cert,upgrades,helper))
                addons.inspect_archive(unsigned,item)
                output=args.out/('realme-'+name+'-'+str(config['lineage'])+'-'+spec['versions'][name]+'-'+action+'.zip')
                proof=sign(unsigned,output,args.android_tree,args.signing_dir,scratch)
                inspection=addons.inspect_archive(output,item)
                rows.append({'action':action,'name':output.name,'bytes':output.stat().st_size,'sha256':addons.sha(output.read_bytes()),'signature':proof,'inspection':inspection})
                if action in ('check','install'):children[name+'-'+action]=output.read_bytes()
            report['profiles'][name]={'changed_files':sorted(p for p in files if files[p]!=original[p]),'artifacts':rows}
    # Retain a direct Camera-only installer for recovery troubleshooting. The main
    # install ZIP also updates an exactly recognized existing Photos installation.
    row=next(r for r in report['profiles']['camera']['artifacts'] if r['action']=='install')
    direct=args.out/row['name'];direct_name=direct.name.replace('-install.zip','-camera-only-install.zip')
    direct.rename(args.out/direct_name)
    report['camera_only']={**row,'name':direct_name}
    budget=(2*max_payload+max(map(len,children.values()))+1023)//1024+98304
    with tempfile.TemporaryDirectory(dir=args.scratch) as temporary:
        scratch=Path(temporary);unsigned=scratch/'coordinated.zip'
        unsigned.write_bytes(coordinated(children,budget))
        proof=sign(unsigned,direct,args.android_tree,args.signing_dir,scratch)
    report['profiles']['camera']['artifacts']=[r for r in report['profiles']['camera']['artifacts'] if r['action']!='install']+[{
        'action':'install','name':direct.name,'bytes':direct.stat().st_size,'sha256':addons.sha(direct.read_bytes()),
        'signature':proof,'kind':'coordinated-camera-lens','scratch_kib':budget}]
    (args.out/'build-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
