"""Prepare a pinned Gallery11 follow-up guarding the optional UAH scheduling API.

Keep drawing/native/media code and supported OEM hint behavior unchanged. This
tool neither installs a package nor supplies fake performance services or grants.
"""
import argparse
import json
from pathlib import Path
import subprocess
import zipfile

from bootstrap_gallery_dex import verified_paths
from oem_gallery_engine import replace_method, sha
from oem_sdk import definitions, select_definitions

ROOT=Path(__file__).resolve().parents[1]
INPUT_SHA='34f07b3226b0cf01c3a2c101a608dc35cfd11e16ff9fa5f5dd030735fa25532a'
TARGET='Lcom/oplus/aiunit/vision/m58;'
HELPER='Lrealme/gallery/compat/OptionalOemPerformance;'
METHOD='.method public final b(I)V'
SOURCE=ROOT/'tools/oem_gallery_compat/OptionalOemPerformance.java'


def patch_latency(text):
    if HELPER in text:
        raise ValueError('performance guard already present')

    def patch(body):
        anchor='    :cond_c\n    const-string v0, "uah handle is released"'
        if (body.count('    .registers 7')!=1 or body.count(anchor)!=1
                or body.count('Lcom/oplus/uah/UAHResClient;->get(')!=1
                or body.count('Lcom/oplus/uah/UAHResClient;->acquireEvent(')!=1
                or body.count('Lcom/oplus/uah/UAHResClient;->release(I)V')!=2):
            raise ValueError('unreviewed UAH hint method')
        added=('    invoke-static {}, '+HELPER+'->available()Z\n\n'
               '    move-result v0\n\n'
               '    if-nez v0, :realme_uah_supported\n\n'
               '    return-void\n\n'
               '    :realme_uah_supported\n')
        return body.replace(anchor,'    :cond_c\n'+added+'    const-string v0, "uah handle is released"')
    return replace_method(text,METHOD,patch)


def prepare(apk,android,out,work):
    if sha(apk.read_bytes())!=INPUT_SHA:
        raise ValueError('unreviewed Gallery predecessor')
    if work.exists() or out.exists():
        raise ValueError('refusing to overwrite candidate')
    work.mkdir(parents=True);java=android/'prebuilts/jdk/jdk21/linux-x86/bin'
    smali=android/'prebuilts/extract-tools/common/smali';sdk=android/'prebuilts/sdk/31/public/android.jar'
    sequence=0

    def run(args):
        nonlocal sequence
        sequence+=1
        p=subprocess.run(list(map(str,args)),capture_output=True,timeout=180)
        (work/f'command-{sequence}.txt').write_bytes(p.stdout+p.stderr);p.check_returncode();return p

    with zipfile.ZipFile(apk) as z:
        if 'classes15.dex' in z.namelist() or not {'classes13.dex','classes14.dex'}<=set(z.namelist()):
            raise ValueError('unreviewed multidex layout')
        old=z.read('classes2.dex')
    names={n for n,_ in definitions(old)}
    if TARGET not in names or HELPER in names:
        raise ValueError('unreviewed target/helper ownership')
    (work/'original.dex').write_bytes(old);(work/'selected.dex').write_bytes(select_definitions(old,{TARGET}))
    run([java/'java','-Xmx512m','-jar',smali/'baksmali.jar','disassemble','--api','31','--output',work/'smali',work/'selected.dex'])
    path=work/'smali'/Path(TARGET[1:-1]+'.smali');before=path.read_text();(work/'before.smali').write_text(before);path.write_text(patch_latency(before))
    run([java/'java','-Xmx512m','-jar',smali/'smali.jar','assemble','--api','31','--output',work/'adapter.dex',work/'smali'])
    classes=work/'classes';classes.mkdir();helpers=work/'helpers';helpers.mkdir()
    run([java/'javac','--release','8','-cp',sdk,'-d',classes,SOURCE])
    run([java/'java','-Xmx512m','-cp',android/'prebuilts/r8/r8.jar','com.android.tools.r8.D8','--lib',sdk,'--min-api','31','--output',helpers,*sorted(classes.rglob('*.class'))])
    if {n for n,_ in definitions((helpers/'classes.dex').read_bytes())}!={HELPER}:
        raise ValueError('unexpected helper class set')
    library=':'.join(map(str,[*verified_paths(),smali/'baksmali.jar']));host=work/'host';host.mkdir()
    run([java/'javac','--release','17','-cp',library,'-d',host,ROOT/'tools/oem_gallery_compat/GalleryDexRelocate.java',ROOT/'tools/oem_gallery_compat/GalleryDexSemanticCheck.java'])
    run([java/'java','-Xmx1g','-cp',str(host)+':'+library,'GalleryDexRelocate',work/'original.dex',work/'remaining.dex',work/'moved.dex',TARGET,work/'adapter.dex',helpers/'classes.dex',HELPER])
    remaining=(work/'remaining.dex').read_bytes();moved=(work/'moved.dex').read_bytes()
    if ({n for n,_ in definitions(remaining)}!=names-{TARGET}
            or {n for n,_ in definitions(moved)}!={TARGET,HELPER}):
        raise ValueError('class ownership differs')
    (work/'before-remaining.dex').write_bytes(select_definitions(old,names-{TARGET}))
    cp=str(host)+':'+str(smali/'baksmali.jar')
    result=run([java/'java','-Xmx1g','-cp',cp,'GalleryDexSemanticCheck',work/'before-remaining.dex',work/'remaining.dex',work/'semantic-diff','--normalize-defaults'])
    comparison=json.loads(result.stdout)
    if not comparison['matching'] or comparison['classes']!=len(names)-1:
        raise ValueError('unaffected original classes differ')
    (work/'final-target.dex').write_bytes(select_definitions(moved,{TARGET}))
    result=run([java/'java','-Xmx512m','-cp',cp,'GalleryDexSemanticCheck',work/'adapter.dex',work/'final-target.dex',work/'target-diff'])
    if not json.loads(result.stdout)['matching']:
        raise ValueError('reviewed adapter differs after relocation')
    with zipfile.ZipFile(apk) as source,zipfile.ZipFile(out,'x') as target:
        for info in source.infolist():
            upper=info.filename.upper()
            if upper.startswith('META-INF/') and (upper.endswith(('.SF','.RSA','.DSA','.EC')) or upper=='META-INF/MANIFEST.MF'):continue
            target.writestr(info,remaining if info.filename=='classes2.dex' else source.read(info.filename))
        original=source.getinfo('classes2.dex');info=zipfile.ZipInfo('classes15.dex',original.date_time)
        for key in ['compress_type','create_system','external_attr','internal_attr']:setattr(info,key,getattr(original,key))
        target.writestr(info,moved)
    report={'input_sha256':INPUT_SHA,'unsigned_apk_sha256':sha(out.read_bytes()),'helper_source_sha256':sha(SOURCE.read_bytes()),'semantic_comparison':comparison,'exact_adapter_retained':True,'added_dex':'classes15.dex','scope':'Unsigned candidate guards complete optional public UAH API before touching it. When absent, omit only OEM scheduling hints; original drawing/native/media and supported OEM hint path remain. No service shim, fake resource handle, privileged grant, policy/global property or device action. Actual strokes/Undo/Save copy and prior feature regressions require trial.'}
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['apk','android','out','work']:p.add_argument('--'+key,type=Path,required=True)
    args=p.parse_args();print(json.dumps(prepare(args.apk,args.android,args.out,args.work)))
