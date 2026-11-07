"""Build the pinned API37 Camera/Photos formats candidate from signed releases.

Inputs stay private. This tool performs no installation or device operation.
The existing child installer retains its partition, peer, rollback and OTA guards.
"""
import argparse
import io
import json
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import zipfile

import oem_addons as addons
import oem_coinstall as coinstall
import oem_lens_bundle as lens
from verify_signapk_ota import signer_cert

ROOT=Path(__file__).resolve().parents[1]
SDK=lens.SDK


def bound_json(spec):
    path=ROOT/addons.safe_path(spec['source'])
    if not path.resolve().is_relative_to((ROOT/'configs').resolve()) or addons.sha(path.read_bytes())!=spec['sha256']:
        raise ValueError('changed source contract')
    return json.loads(path.read_text())


def read_release(path, spec, name, scratch):
    config=bound_json(spec['config'])
    if addons.sha(path.read_bytes())!=spec['sha256']:
        raise ValueError('changed source archive')
    member=spec.get('member')
    if member:
        # Authenticate the outer signed coordinator before unwrapping its exact
        # direct Camera child. Never execute an archive while preparing a build.
        with zipfile.ZipFile(path) as z:
            proof=signer_cert(path,z,scratch,'openssl')
            expected=addons.sha(ssl.PEM_cert_to_DER_cert((ROOT/'certs/rmx3301-releasekey.x509.pem').read_text()))
            if proof['cms_signer_sha256']!=expected or not proof['signer_matches_otacert']:
                raise ValueError('outer recovery signer differs')
            data=z.read(member['path'])
            line=addons.sha(data)+'  '+member['path']
            if (addons.sha(data)!=member['sha256'] or
                    z.read('children.sha256').decode().splitlines().count(line)!=1):
                raise ValueError('coordinator child differs')
        direct=scratch/'direct.zip';direct.write_bytes(data)
    else:
        direct=path
    files,helper=lens.read_source(direct,config,name,
        member['sha256'] if member else spec['sha256'],scratch)
    return config,files,helper


def patch_payload(item, original, sdk, gallery_apk):
    if addons.sha(sdk)!=item['formats_sdk_sha256']:
        raise ValueError('unreviewed formats SDK')
    old_apks=[p for p in original if p.endswith('/base.apk')]
    if len(old_apks)!=1:raise ValueError('one source APK required')
    old_root=old_apks[0].removesuffix('/base.apk');new_root=addons.app_path(item)
    if item['id']=='camera':
        if old_root!=new_root or addons.sha(original[old_apks[0]])!=item['sha256']:
            raise ValueError('Camera APK or placement changed')
    elif addons.sha(gallery_apk)!=item['sha256']:
        raise ValueError('unreviewed prepared Photos APK')
    result={new_root+p[len(old_root):] if p.startswith(old_root+'/') else p:data
            for p,data in original.items()}
    if item['id']=='photos':result[new_root+'/base.apk']=gallery_apk
    result[SDK]=sdk
    receipt_path='product/etc/realme-oem-addons/'+item['id']+'.json'
    receipt=json.loads(result[receipt_path])
    if receipt['shared_sdk_sha256']!=addons.sha(original[SDK]):
        raise ValueError('source receipt SDK differs')
    receipt.update(addon_release=item['addon_release'],sha256=item['sha256'],
                   shared_sdk_sha256=addons.sha(sdk))
    for field,key in [('app_compatibility','app_compatibility_contract_sha256'),
                      ('runtime_contract','runtime_contract_sha256')]:
        if field in item:receipt[key]=item[field]['sha256']
    result[receipt_path]=(json.dumps(receipt,sort_keys=True,indent=2)+'\n').encode()
    hook=coinstall.HOOKS[item['id']];result.pop(hook)
    result[hook]=addons.hook(item,result)
    # Every native library, LUT, permission, policy and other resource must remain
    # byte exact. Only Photos' content-addressed application root may relocate.
    changed={SDK,receipt_path,hook}
    if item['id']=='photos':changed.add(new_root+'/base.apk')
    for path,data in original.items():
        target=new_root+path[len(old_root):] if path.startswith(old_root+'/') else path
        if target not in changed and result.get(target)!=data:
            raise ValueError('formats patch exceeds reviewed scope')
    if len(result)!=len(original):raise ValueError('unexpected payload members')
    addons.validate_gallery_runtime(item,{p:addons.sha(d) for p,d in result.items()})
    return result


def coordinated(children,scratch_kib):
    expected={name+'-'+action for name in ('camera','photos') for action in ('check','install')}
    expected|={f'old{n}-{name}-{action}' for n in (0,1) for name,action in
               [('camera','check'),('photos','check'),('photos','remove'),('photos','install')]}
    if set(children)!=expected or not 32768<=scratch_kib<2**31:
        raise ValueError('incomplete formats coordinator')
    index=''.join(addons.sha(data)+'  packages/'+name+'.zip\n' for name,data in sorted(children.items())).encode()
    script=(ROOT/'tools/oem_addons/formats-upgrade.sh').read_bytes()
    for key,value in {'INDEX_SHA':addons.sha(index),'SCRATCH_KIB':str(scratch_kib),
                      'CHILD_CASE':'|'.join('packages/'+n+'.zip' for n in sorted(children))}.items():
        script=script.replace(('@'+key+'@').encode(),value.encode())
    if re.search(rb'@[A-Z_]+@',script):raise ValueError('unresolved formats template')
    subprocess.run(['sh','-n'],input=script,check=True,capture_output=True)
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as z:
        for name,data in sorted({'children.sha256':index,
            'META-INF/com/google/android/update-binary':script,
            'META-INF/com/google/android/updater-script':b'# Camera/Photos formats update\n',
            **{'packages/'+n+'.zip':v for n,v in children.items()}}.items()):
            info=zipfile.ZipInfo(name,(2009,1,1,0,0,0));info.external_attr=0o100644<<16
            z.writestr(info,data)
    return output.getvalue()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('config','source-dir','sdk','gallery-apk','android-tree','signing-dir','out','scratch'):
        parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();config=json.loads(args.config.read_text());spec=config['formats_patch']
    sdk=args.sdk.read_bytes();apk=args.gallery_apk.read_bytes()
    if addons.sha(sdk)!=bound_json(spec['sdk_contract'])['output_sdk_sha256']:
        raise ValueError('SDK differs from recipe')
    args.out.mkdir(parents=True,exist_ok=False);args.scratch.mkdir(parents=True,exist_ok=True)
    cert=(ROOT/'certs/rmx3301-releasekey.x509.pem').read_bytes()
    children={};cohorts=[];max_payload=0;report={'status':'offline candidate; device install and UAT pending','profiles':{}}
    for index,cohort in enumerate(spec['sources']):
        parts={}
        for name in ('camera','photos'):
            binding=cohort[name]
            with tempfile.TemporaryDirectory(dir=args.scratch) as directory:
                old,files,helper=read_release(args.source_dir/binding['archive'],binding,name,Path(directory))
            item=lens.profile(old,name);parts[name]=(old,files,helper)
            max_payload=max(max_payload,sum(map(len,files.values())))
            check_item=dict(item,installed_space_budget=True)
            children[f'old{index}-{name}-check']=addons.archive_bytes(check_item,files,'check',cert,None,helper)
            if name=='photos':
                for action in ('remove','install'):
                    children[f'old{index}-photos-{action}']=addons.archive_bytes(item,files,action,cert,None,helper)
        cohorts.append(parts)
    if len(cohorts)!=2:raise ValueError('exactly two reviewed predecessors required')
    for name in ('camera','photos'):
        item=lens.profile(config,name);original=cohorts[-1][name][1];helper=cohorts[-1][name][2]
        files=patch_payload(item,original,sdk,apk);max_payload=max(max_payload,sum(map(len,files.values())))
        upgrades=addons.checked_upgrade_history(item,files,bound_json(item['upgrade_history']))
        rows=[]
        for action in ('check','remove','install'):
            with tempfile.TemporaryDirectory(dir=args.scratch) as directory:
                scratch=Path(directory);unsigned=scratch/'unsigned.zip'
                unsigned.write_bytes(addons.archive_bytes(item,files,action,cert,upgrades,helper))
                addons.inspect_archive(unsigned,item)
                output=args.out/f'realme-{name}-{config["lineage"]}-{config["release"]}-{action}.zip'
                proof=lens.sign(unsigned,output,args.android_tree,args.signing_dir,scratch)
                inspection=addons.inspect_archive(output,item)
            rows.append(dict(action=action,name=output.name,bytes=output.stat().st_size,
                             sha256=addons.sha(output.read_bytes()),signature=proof,inspection=inspection))
            if action in ('check','install'):children[name+'-'+action]=output.read_bytes()
        report['profiles'][name]={'artifacts':rows,'file_count':len(files)}
    row=next(r for r in report['profiles']['camera']['artifacts'] if r['action']=='install')
    direct=args.out/row['name'];only=direct.with_name(direct.name.replace('-install.zip','-camera-only-install.zip'))
    direct.rename(only);report['camera_only']={**row,'name':only.name}
    budget=(2*max_payload+max(map(len,children.values()))+1023)//1024+98304
    with tempfile.TemporaryDirectory(dir=args.scratch) as directory:
        scratch=Path(directory);unsigned=scratch/'coordinator.zip';unsigned.write_bytes(coordinated(children,budget))
        proof=lens.sign(unsigned,direct,args.android_tree,args.signing_dir,scratch)
    report['profiles']['camera']['artifacts']=[r for r in report['profiles']['camera']['artifacts'] if r['action']!='install']+[
        dict(action='install',name=direct.name,bytes=direct.stat().st_size,sha256=addons.sha(direct.read_bytes()),
             signature=proof,kind='coordinated-formats',scratch_kib=budget)]
    (args.out/'build-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'out':str(args.out),'coordinated_scratch_kib':budget}))


if __name__=='__main__':main()
