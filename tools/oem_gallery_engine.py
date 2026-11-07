"""Prepare pinned Gallery10 follow-up for Markup haptics and packaged Remover code.

No signing, device action or policy relaxation. The recovery profile must supply
the exact reviewed native provider beside Gallery's other read-only libraries.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

from bootstrap_gallery_dex import verified_paths
from oem_sdk import definitions, select_definitions

ROOT = Path(__file__).resolve().parents[1]
INPUT_SHA = 'ed00cdb70cc98563e4f69c60b72d59234c19aee33860aa306cb45bf863a60b04'
NATIVE_SHA = '183618cce6537e99dfee91136a998a1f0da6c89584b8142ea6047cde4305d1dd'
TOOLKIT = 'Lcom/oplusos/vfxsdk/doodleengine/toolkit/Toolkit;'
MAGIC = 'Lcom/oplus/tbluniformeditor/plugins/magiceliminate/function/MagicEliminateAlgoImpl;'
HAPTICS = 'Lrealme/gallery/compat/OptionalOemHaptics;'
NATIVE = 'Lrealme/gallery/compat/RemoverNativeCompat;'
CONSTRUCTOR = '.method public constructor <init>(Landroid/content/Context;Landroid/util/AttributeSet;I)V'
LOADER = '.method private loadLibrary(Ljava/lang/String;)Z'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def replace_method(text, marker, transform):
    if text.count(marker) != 1:
        raise ValueError('unreviewed target method')
    start = text.index(marker)
    end = text.index('.end method', start) + len('.end method')
    return text[:start] + transform(text[start:end]) + text[end:]


def patch_toolkit(text):
    if HAPTICS in text:
        raise ValueError('haptic guard already present')

    def patch(body):
        anchors = re.findall(r'    \.line 43\n    :try_start_[0-9a-f]+\n',body)
        fallback = re.findall(r'    (:goto_[0-9a-f]+)\n(?:\n)?    sget-object v0, '+re.escape(TOOLKIT)+r'->mLinearmotorVibrator:',body)
        if (len(anchors) != 1 or len(fallback) != 1 or body.count('    .registers 30') != 1
                or 'Lrealme/gallery/compat/DoodleTheme;->context' not in body
                or body.count('    const-string v2, "vibrator"') != 1):
            raise ValueError('unreviewed Toolkit haptic fallback')
        addition = ('    invoke-static {}, '+HAPTICS+'->available()Z\n\n'
                    '    move-result v0\n\n'
                    '    if-eqz v0, '+fallback[0]+'\n\n')
        return body.replace(anchors[0], addition+anchors[0])
    return replace_method(text, CONSTRUCTOR, patch)


def patch_loader(text):
    if NATIVE in text:
        raise ValueError('native adapter already present')

    def patch(body):
        anchor = '    invoke-static {p1}, Ljava/lang/System;->load(Ljava/lang/String;)V'
        if (body.count(anchor) != 1 or body.count('    .registers 4') != 1
                or '.catch Ljava/lang/UnsatisfiedLinkError;' not in body):
            raise ValueError('unreviewed native loader')
        return body.replace(anchor, '    invoke-static {p1}, '+NATIVE+'->load(Ljava/lang/String;)V')
    return replace_method(text, LOADER, patch)


def prepare(apk, android, out, work):
    if sha(apk.read_bytes()) != INPUT_SHA:
        raise ValueError('unreviewed Gallery predecessor')
    if work.exists() or out.exists():
        raise ValueError('refusing to overwrite candidate')
    work.mkdir(parents=True)
    java = android/'prebuilts/jdk/jdk21/linux-x86/bin'
    smali = android/'prebuilts/extract-tools/common/smali'
    sdk = android/'prebuilts/sdk/31/public/android.jar'
    host = work/'host'; host.mkdir()
    library = ':'.join(map(str, [*verified_paths(), smali/'baksmali.jar']))
    sequence = 0

    def run(args):
        nonlocal sequence
        sequence += 1
        result = subprocess.run(list(map(str, args)), capture_output=True, timeout=180)
        (work/f'command-{sequence}.txt').write_bytes(result.stdout+result.stderr)
        result.check_returncode()
        return result

    run([java/'javac', '--release', '17', '-cp', library, '-d', host,
         ROOT/'tools/oem_gallery_compat/GalleryDexRelocate.java',
         ROOT/'tools/oem_gallery_compat/GalleryDexSemanticCheck.java'])
    changed = {}; added = {}; reports = []
    entries = [('classes2.dex', 'classes13.dex', TOOLKIT, HAPTICS, patch_toolkit),
               ('classes10.dex', 'classes14.dex', MAGIC, NATIVE, patch_loader)]
    with zipfile.ZipFile(apk) as archive:
        members = set(archive.namelist())
        if not {'classes11.dex', 'classes12.dex'} <= members or {'classes13.dex', 'classes14.dex'} & members:
            raise ValueError('unreviewed multidex layout')
        all_names = {name for member in members if re.fullmatch(r'classes\d*\.dex', member)
                     for name, _ in definitions(archive.read(member))}
        if {HAPTICS, NATIVE} & all_names:
            raise ValueError('existing helper must not be replaced')
        for original, destination, target, helper, transform in entries:
            stage = work/original; stage.mkdir()
            old = archive.read(original); names = {n for n, _ in definitions(old)}
            (stage/'original.dex').write_bytes(old)
            (stage/'selected.dex').write_bytes(select_definitions(old, {target}))
            run([java/'java', '-Xmx512m', '-jar', smali/'baksmali.jar', 'disassemble',
                 '--api', '31', '--output', stage/'smali', stage/'selected.dex'])
            path = stage/'smali'/Path(target[1:-1]+'.smali'); before = path.read_text()
            path.write_text(transform(before)); (stage/'before.smali').write_text(before)
            run([java/'java', '-Xmx512m', '-jar', smali/'smali.jar', 'assemble', '--api', '31',
                 '--output', stage/'adapter.dex', stage/'smali'])
            classes = stage/'classes'; classes.mkdir(); helpers = stage/'helpers'; helpers.mkdir()
            source = ROOT/'tools/oem_gallery_compat'/Path(helper[1:-1]).name
            source = source.with_suffix('.java')
            run([java/'javac', '--release', '8', '-cp', sdk, '-d', classes, source])
            run([java/'java', '-Xmx512m', '-cp', android/'prebuilts/r8/r8.jar',
                 'com.android.tools.r8.D8', '--lib', sdk, '--min-api', '31', '--output', helpers,
                 *sorted(classes.rglob('*.class'))])
            if {n for n, _ in definitions((helpers/'classes.dex').read_bytes())} != {helper}:
                raise ValueError('unexpected helper class set')
            run([java/'java', '-Xmx1g', '-cp', str(host)+':'+library, 'GalleryDexRelocate',
                 stage/'original.dex', stage/'remaining.dex', stage/'moved.dex', target,
                 stage/'adapter.dex', helpers/'classes.dex', helper])
            remaining=(stage/'remaining.dex').read_bytes();moved=(stage/'moved.dex').read_bytes()
            if ({n for n,_ in definitions(remaining)} != names-{target}
                    or {n for n,_ in definitions(moved)} != {target,helper}):
                raise ValueError('class ownership differs')
            (stage/'before-remaining.dex').write_bytes(select_definitions(old,names-{target}))
            cp=str(host)+':'+str(smali/'baksmali.jar')
            result=run([java/'java','-Xmx1g','-cp',cp,'GalleryDexSemanticCheck',
                        stage/'before-remaining.dex',stage/'remaining.dex',stage/'semantic-diff',
                        '--normalize-defaults'])
            comparison=json.loads(result.stdout)
            if not comparison['matching'] or comparison['classes']!=len(names)-1:
                raise ValueError('unaffected original classes differ')
            (stage/'final-target.dex').write_bytes(select_definitions(moved,{target}))
            result=run([java/'java','-Xmx512m','-cp',cp,'GalleryDexSemanticCheck',
                        stage/'adapter.dex',stage/'final-target.dex',stage/'target-diff'])
            if not json.loads(result.stdout)['matching']:
                raise ValueError('reviewed adapter differs after relocation')
            changed[original]=remaining;added[destination]=(archive.getinfo(original),moved)
            reports.append({'original':original,'new_dex':destination,'target':target,
                            'helper':helper,'source_sha256':sha(source.read_bytes()),
                            'semantic_comparison':comparison,'exact_adapter_retained':True})
        with zipfile.ZipFile(out,'x') as result:
            for info in archive.infolist():
                upper=info.filename.upper()
                if upper.startswith('META-INF/') and (upper.endswith(('.SF','.RSA','.DSA','.EC'))
                                                       or upper=='META-INF/MANIFEST.MF'):continue
                result.writestr(info,changed.get(info.filename,archive.read(info.filename)))
            for name,(original,blob) in added.items():
                info=zipfile.ZipInfo(name,original.date_time)
                for attribute in ['compress_type','create_system','external_attr','internal_attr']:
                    setattr(info,attribute,getattr(original,attribute))
                result.writestr(info,blob)
    report={'input_sha256':INPUT_SHA,'unsigned_apk_sha256':sha(out.read_bytes()),'changes':reports,
            'native_provider_sha256':NATIVE_SHA,'native_provider_name':'librealme_cv_inpainting_v10.so',
            'scope':'Unsigned optional Gallery candidate; complete OEM haptics API detection retains the original Android vibrator fallback. RMX3301/Android36+ known Remover module path uses a pinned native provider supplied read-only by the signed recovery bundle. Other devices/paths/older versions retain original System.load. Normal model download/consent/checksum/UI gates, existing alpha10 fixes, manifest/resources/JNI and SELinux policy remain unchanged. Native deployment/initialization/editing and Markup UI acceptance are separate.'}
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ['apk','android','out','work']:parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();print(json.dumps(prepare(args.apk,args.android,args.out,args.work)))
