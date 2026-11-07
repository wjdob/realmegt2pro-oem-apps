"""Prepare version-bound, unsigned Gallery follow-up candidates; no device action.

Currently implements guarded Markup context, modern review entry and resolver icons. Other bundled issues
remain under audit. This output is not a signed recovery package or acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from oem_sdk import definitions, select_definitions
from oem_camera_window import abi
from oem_gallery_manifest import add_review_entry,add_visibility_queries
from oem_gallery_icons import prepare_icon_dex
from oem_gallery_dex import merge_class
from oem_gallery_share import prepare_share_dex

ROOT=Path(__file__).resolve().parents[1]
INPUT_SHA='52979c9f1ac7a86c928281b32ca6652a0b1893d66b7a7a541a2025ddb27560ef'
TYPE='Lcom/oplusos/vfxsdk/doodleengine/toolkit/Toolkit;'
HELPER='Lrealme/gallery/compat/DoodleTheme;'
REVIEW='Lrealme/gallery/compat/ReviewActivity;'
REQUEST='Lrealme/gallery/compat/ReviewRequest;'
ICON='Lrealme/gallery/compat/ResolverFallback;'
SOURCE=ROOT/'tools/oem_gallery_compat/DoodleTheme.java'


def sha(data):return hashlib.sha256(data).hexdigest()


def patch_toolkit(text):
    marker='.method public constructor <init>(Landroid/content/Context;Landroid/util/AttributeSet;I)V'
    if text.count(marker)!=1 or HELPER in text:
        raise ValueError('unexpected Toolkit preimage')
    start=text.index(marker);end=text.index('.end method',start)+len('.end method')
    body=text[start:end];needle='    move-object/from16 v1, p0'
    if body.count('    .registers 30')!=1 or body.count(needle)!=1:
        raise ValueError('unexpected constructor register layout')
    prefix=('    invoke-static/range {p1 .. p1}, '+HELPER+
            '->context(Landroid/content/Context;)Landroid/content/Context;\n\n'
            '    move-result-object p1\n\n')
    return text[:start]+body.replace(needle,prefix+needle)+text[end:]


def prepare(apk,android,out,work):
    if sha(apk.read_bytes())!=INPUT_SHA:raise ValueError('unreviewed Gallery APK')
    if out.exists() or work.exists():raise ValueError('refusing to overwrite candidate')
    work.mkdir(parents=True);java=android/'prebuilts/jdk/jdk21/linux-x86/bin'
    smali=android/'prebuilts/extract-tools/common/smali';sdk=android/'prebuilts/sdk/31/public/android.jar'
    with zipfile.ZipFile(apk) as z:old=z.read('classes2.dex')
    names={n for n,_ in definitions(old)}
    if TYPE not in names or {HELPER,REVIEW,REQUEST,ICON}&names:raise ValueError('unexpected class set')
    selected=work/'toolkit.dex';selected.write_bytes(select_definitions(old,{TYPE}))
    run=lambda args:subprocess.run(list(map(str,args)),check=True,capture_output=True,timeout=180)
    run([java/'java','-Xmx512m','-jar',smali/'baksmali.jar','disassemble','--api','31','--output',work/'smali',selected])
    path=work/'smali'/Path(TYPE[1:-1]+'.smali');before=path.read_text();(work/'Toolkit-before.smali').write_text(before);path.write_text(patch_toolkit(before))
    adapter=work/'adapter.dex';run([java/'java','-Xmx512m','-jar',smali/'smali.jar','assemble','--api','31','--output',adapter,work/'smali'])
    classes=work/'classes';classes.mkdir();run([java/'javac','--release','8','-cp',sdk,'-d',classes,SOURCE,SOURCE.parent/'ReviewActivity.java',SOURCE.parent/'ReviewRequest.java',SOURCE.parent/'ResolverFallback.java'])
    dest=work/'helpers';dest.mkdir()
    run([java/'java','-Xmx1g','-cp',android/'prebuilts/r8/r8.jar','com.android.tools.r8.D8','--lib',sdk,'--min-api','31','--output',dest,classes/'realme/gallery/compat/DoodleTheme.class',classes/'realme/gallery/compat/ReviewActivity.class',classes/'realme/gallery/compat/ReviewRequest.class',classes/'realme/gallery/compat/ResolverFallback.class'])
    if sorted(p.name for p in dest.iterdir())!=['classes.dex']:raise ValueError('unexpected DEX split')
    new,semantic=merge_class(old,adapter,TYPE,android,work/'merge',dest/'classes.dex')
    if {n for n,_ in definitions(new)}!=names|{HELPER,REVIEW,REQUEST,ICON}:raise ValueError('class set differs')
    if abi(old,{TYPE})!=abi(new,{TYPE}):raise ValueError('Toolkit ABI differs')
    with zipfile.ZipFile(apk) as source:
        icons,icon_report=prepare_icon_dex(source.read('classes7.dex'),android,work/'icons')
        sharing,sharing_report=prepare_share_dex(source.read('classes5.dex'),android,work/'sharing')
    with zipfile.ZipFile(apk) as source,zipfile.ZipFile(out,'x') as target:
        manifest=add_visibility_queries(add_review_entry(source.read('AndroidManifest.xml')))
        for info in source.infolist():
            name=info.filename.upper()
            if name.startswith('META-INF/') and (name.endswith(('.SF','.RSA','.DSA','.EC')) or name=='META-INF/MANIFEST.MF'):continue
            target.writestr(info,new if info.filename=='classes2.dex' else icons if info.filename=='classes7.dex' else sharing if info.filename=='classes5.dex' else manifest if info.filename=='AndroidManifest.xml' else source.read(info.filename))
    report=dict(input_sha256=INPUT_SHA,unsigned_apk_sha256=sha(out.read_bytes()),classes=len(names),semantic_comparison=semantic,icon_semantic_comparison=icon_report,share_semantic_comparison=sharing_report,source_sha256=sha(SOURCE.read_bytes()),review_source_sha256=sha((SOURCE.parent/'ReviewActivity.java').read_bytes()),request_source_sha256=sha((SOURCE.parent/'ReviewRequest.java').read_bytes()),icon_source_sha256=sha((SOURCE.parent/'ResolverFallback.java').read_bytes()),manifest_sha256=sha(manifest),scope='Unsigned follow-up intermediate: guarded Markup theme, modern review, resolver-icon fallback, cropper/video query visibility and read-only SEND grants. Unaffected class bodies/metadata compared directly to original, with other DEX/resources/native bytes retained. No installed UI acceptance or complete follow-up remediation claimed.')
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['apk','android','out','work']:p.add_argument('--'+key,type=Path,required=True)
    args=p.parse_args();print(json.dumps(prepare(args.apk,args.android,args.out,args.work)))
