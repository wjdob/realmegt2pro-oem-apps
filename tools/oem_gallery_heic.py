"""Add public HEIC export when the pinned optional Gallery lacks its OEM capability.

Keep the original OEM path, user conversion switch, share/provider transactions,
metadata/privacy processing and all unrelated classes/resources/native bytes.
This prepares an unsigned APK; it performs no signing or device operation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from oem_gallery_dex import merge_class
from oem_gallery_engine import replace_method
from oem_sdk import definitions, select_definitions

ROOT = Path(__file__).resolve().parents[1]
INPUT_SHA = '644e0df3f250cf6e97163664b6cf207617a817430daa237c48f2d6a185ad600e'
CAPABILITY = 'Lcom/oplus/aiunit/vision/wr6;'
CONVERTER = 'Lcom/oplus/aiunit/vision/m53;'
HELPER = 'Lrealme/gallery/compat/HeicCompatibility;'
SOURCE = ROOT/'tools/oem_gallery_compat/HeicCompatibility.java'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch_capability(text):
    def transform(body):
        anchor = '    invoke-static {}, Lcom/oplus/aiunit/vision/m55;->q()Z'
        if (HELPER in body or body.count('    .registers 10') != 1
                or body.count(anchor) != 1
                or body.count('    sput-boolean v7, '+CAPABILITY+'->b:Z') != 1):
            raise ValueError('unreviewed HEIC initialization')
        addition = f'''
    invoke-static {{p0}}, {HELPER}->available(Landroid/content/Context;)Z

    move-result v0

    if-eqz v0, :realme_heic_original_initialization

    const/4 v0, 0x1

    sput-boolean v0, {CAPABILITY}->c:Z

    sput-boolean v0, {CAPABILITY}->b:Z

    sget-object v0, {CAPABILITY}->e:Ljava/lang/String;

    invoke-static {{v0}}, {HELPER}->enabled(Ljava/lang/String;)Z

    move-result v0

    sput-boolean v0, {CAPABILITY}->d:Z

    invoke-static {{p0}}, Landroidx/preference/PreferenceManager;->getDefaultSharedPreferences(Landroid/content/Context;)Landroid/content/SharedPreferences;

    move-result-object v0

    const-string v1, "pref_heif_scan_enable"

    const/4 v2, 0x1

    invoke-interface {{v0, v1, v2}}, Landroid/content/SharedPreferences;->getBoolean(Ljava/lang/String;Z)Z

    move-result v0

    sput-boolean v0, {CAPABILITY}->f:Z

    return-void

    :realme_heic_original_initialization
'''
        return body.replace(anchor, addition + anchor)
    return replace_method(text, '.method public static final b(Landroid/content/Context;)V', transform)


def patch_converter(text):
    def transform(body):
        anchor = '    invoke-static {p0, p1, p2}, Lcom/heytap/addon/media/OplusHeifConverter;->convertHeifToJpegFromStream(Ljava/io/InputStream;ILjava/io/OutputStream;)Z'
        if HELPER in body or body.count('    .registers 4') != 1 or body.count(anchor) != 1:
            raise ValueError('unreviewed HEIC stream converter')
        addition = f'''    sget-object v0, Lcom/oplus/aiunit/vision/n33;->a:Landroid/content/Context;

    invoke-static {{v0}}, {HELPER}->available(Landroid/content/Context;)Z

    move-result v0

    if-eqz v0, :realme_heic_original_converter

    invoke-static {{p0, p1, p2}}, {HELPER}->convert(Ljava/io/InputStream;ILjava/io/OutputStream;)Z

    move-result v0

    return v0

    :realme_heic_original_converter
'''
        return body.replace(anchor, addition + anchor)
    return replace_method(text, '.method public static final b(Ljava/io/InputStream;ILjava/io/OutputStream;)Z', transform)


def prepare(apk, android, output, work):
    if sha(apk.read_bytes()) != INPUT_SHA:
        raise ValueError('unreviewed Gallery predecessor')
    if work.exists() or output.exists():
        raise ValueError('refusing to overwrite candidate')
    work.mkdir(parents=True)
    java = android/'prebuilts/jdk/jdk21/linux-x86/bin'
    smali = android/'prebuilts/extract-tools/common/smali'
    sdk = android/'prebuilts/sdk/31/public/android.jar'
    commands = []
    def run(args):
        p = subprocess.run(list(map(str, args)), capture_output=True, timeout=180)
        commands.append({'args':list(map(str,args)), 'exit_code':p.returncode})
        (work/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        if p.returncode: raise RuntimeError(p.stderr.decode(errors='replace')[-2000:])
        return p
    classes = work/'classes'; classes.mkdir()
    helpers = work/'helpers'; helpers.mkdir()
    run([java/'javac','--release','8','-cp',sdk,'-d',classes,SOURCE])
    run([java/'java','-Xmx512m','-cp',android/'prebuilts/r8/r8.jar','com.android.tools.r8.D8',
         '--lib',sdk,'--min-api','31','--output',helpers,*sorted(classes.rglob('*.class'))])
    helper_dex = (helpers/'classes.dex').read_bytes()
    if {n for n,_ in definitions(helper_dex)} != {HELPER}:
        raise ValueError('unexpected helper closure')
    changes = {}; comparisons = []; preimages = {}
    with zipfile.ZipFile(apk) as original:
        for dex, target, transform in [('classes.dex',CONVERTER,patch_converter),
                                       ('classes5.dex',CAPABILITY,patch_capability)]:
            old = original.read(dex)
            part = work/dex; part.mkdir()
            selected = part/'selected.dex'; selected.write_bytes(select_definitions(old,{target}))
            stage = part/'smali'
            run([java/'java','-Xmx512m','-jar',smali/'baksmali.jar','disassemble','--api','31',
                 '--output',stage,selected])
            path = stage/(target[1:-1]+'.smali')
            before = path.read_text(); changed = transform(before)
            (part/'before.smali').write_text(before);path.write_text(changed)
            preimages[target] = {'before':sha(before.encode()),'after':sha(changed.encode())}
            adapter = part/'adapter.dex'
            run([java/'java','-Xmx512m','-jar',smali/'smali.jar','assemble','--api','31',
                 '--output',adapter,stage])
            new, report = merge_class(old,adapter,target,android,part/'merge')
            changes[dex] = new; comparisons.append(report)
        if 'classes18.dex' in original.namelist():
            raise ValueError('helper DEX slot occupied')
        with zipfile.ZipFile(output,'x') as candidate:
            for info in original.infolist():
                name = info.filename.upper()
                if name.startswith('META-INF/') and (name.endswith(('.SF','.RSA','.DSA','.EC')) or name=='META-INF/MANIFEST.MF'):
                    continue
                candidate.writestr(info,changes.get(info.filename,original.read(info.filename)))
            info = zipfile.ZipInfo('classes18.dex',original.getinfo('classes5.dex').date_time)
            info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            candidate.writestr(info,helper_dex)
    with zipfile.ZipFile(apk) as original, zipfile.ZipFile(output) as candidate:
        retained = [n for n in candidate.namelist() if n not in {*changes,'classes18.dex'}]
        if any(original.read(n)!=candidate.read(n) for n in retained):
            raise ValueError('unrelated APK bytes changed')
    result = {'input_sha256':INPUT_SHA,'unsigned_apk_sha256':sha(output.read_bytes()),
        'helper_source_sha256':sha(SOURCE.read_bytes()),'helper_dex_sha256':sha(helper_dex),
        'preimages':preimages,'unaffected_classes':comparisons,'other_members_exact':len(retained),
        'scope':'Two exact OEM adapters plus public HEIC stream helper. OEM-capable path and user opt-in remain. Existing cache/provider/metadata/privacy/share transactions retained. No signing, install, permission or policy change.',
        'limits':'Candidate only. Pixel/EXIF/privacy, single/multiple share, cancellation, resource limits and existing feature regressions require qualification.'}
    (work/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('apk','android','output','work'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.apk,args.android,args.output,args.work),indent=2))
