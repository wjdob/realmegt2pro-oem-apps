"""Build the keyed-Lens API37 successor from three authenticated app cohorts.

The formats17 builder and its two-cohort recovery recipe remain reproducible.
This successor changes only SDK/receipt/retention hooks relative to alpha3.
It performs no device operation.
"""
import argparse
import io
import json
import re
import subprocess
import tempfile
from pathlib import Path
import zipfile
import oem_addons as addons
import oem_lens_bundle as lens
from oem_formats_bundle import bound_json, read_release, patch_payload, SDK
from oem_lens_key_audit import inspect_bytes
ROOT=Path(__file__).resolve().parents[1]


def patch_successor(item, original, sdk, apk):
    files=patch_payload(item,original,sdk,apk)
    allowed={SDK,'product/etc/realme-oem-addons/'+item['id']+'.json',
             'system/addon.d/'+item['hook']}
    if set(files)!=set(original) or {p for p in files if files[p]!=original[p]}!=allowed:
        raise ValueError('keyed Lens successor exceeds SDK/receipt/hook scope')
    return files


def coordinated(children,scratch_kib):
    expected={name+'-'+action for name in ('camera','photos') for action in ('check','install')}
    expected|={f'old{n}-{name}-{action}' for n in (0,1,2) for name,action in
               [('camera','check'),('photos','check'),('photos','remove'),('photos','install')]}
    if set(children)!=expected or not 32768<=scratch_kib<2**31:
        raise ValueError('incomplete keyed Lens coordinator')
    index=''.join(addons.sha(data)+'  packages/'+name+'.zip\n' for name,data in sorted(children.items())).encode()
    script=(ROOT/'tools/oem_addons/lens-key-upgrade.sh').read_bytes()
    for key,value in {'INDEX_SHA':addons.sha(index),'SCRATCH_KIB':str(scratch_kib),
                      'CHILD_CASE':'|'.join('packages/'+n+'.zip' for n in sorted(children))}.items():
        script=script.replace(('@'+key+'@').encode(),value.encode())
    if re.search(rb'@[A-Z_]+@',script):raise ValueError('unresolved formats template')
    subprocess.run(['sh','-n'],input=script,check=True,capture_output=True)
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as z:
        for name,data in sorted({'children.sha256':index,
            'META-INF/com/google/android/update-binary':script,
            'META-INF/com/google/android/updater-script':b'# Camera/Photos keyed Lens update\n',
            **{'packages/'+n+'.zip':v for n,v in children.items()}}.items()):
            info=zipfile.ZipInfo(name,(2009,1,1,0,0,0));info.external_attr=0o100644<<16
            z.writestr(info,data)
    return output.getvalue()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('config','source-dir','sdk','gallery-apk','android-tree','signing-dir','out','scratch'):
        parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();config=json.loads(args.config.read_text());spec=config['lens_key_patch']
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
    if len(cohorts)!=3:raise ValueError('exactly three reviewed predecessors required')
    for name in ('camera','photos'):
        item=lens.profile(config,name);original=cohorts[-1][name][1];helper=cohorts[-1][name][2]
        if name=='camera':
            report['native_lens_audit']=inspect_bytes(original[addons.app_path(item)+'/base.apk'])
        files=patch_successor(item,original,sdk,apk)
        max_payload=max(max_payload,sum(map(len,files.values())))
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
             signature=proof,kind='coordinated-keyed-lens',scratch_kib=budget)]
    (args.out/'build-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'out':str(args.out),'coordinated_scratch_kib':budget}))


if __name__=='__main__':main()
