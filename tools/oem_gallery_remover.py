"""Prepare the version-bound Remover request correction; no device action.

Consumes the frozen Gallery-alpha9 candidate, preserving its other bundled fixes.
An API metadata response does not establish engine loading or usable photo removal.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

from oem_sdk import definitions, select_definitions
from bootstrap_gallery_dex import verified_paths

ROOT = Path(__file__).resolve().parents[1]
INPUT_SHA = '1ee40466a2350897c8d502b64d1ccb210684b62cae46c180f69b49bafd5c8ef8'
TARGET = 'Lcom/oplus/gallery/framework/abilities/download/networkrequest/request/GetModelRequest;'
HELPER = 'Lrealme/gallery/compat/ModelRequestCompat;'
METHOD = '.method public constructor <init>(Landroid/content/Context;Lcom/oplus/aiunit/vision/l77;Z)V'
SOURCE = ROOT/'tools/oem_gallery_compat/ModelRequestCompat.java'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def method(text):
    if text.count(METHOD) != 1:
        raise ValueError('unexpected model-request constructor')
    start = text.index(METHOD)
    end = text.index('.end method', start) + len('.end method')
    return start, end, text[start:end]


def patch_request(text):
    start, end, body = method(text)
    anchor = ('    invoke-static {p3}, Lcom/oplus/aiunit/vision/jv6;->d(Z)Ljava/util/Map;\n\n'
              '    move-result-object p2\n')
    if (HELPER in text or body.count(anchor) != 1 or body.count('    .registers 12') != 1
            or body.count('    const-string p3, "aimodeltype"') != 1):
        raise ValueError('unexpected model-request preimage')
    addition = ('\n    invoke-static {p2}, '+HELPER+'->complete(Ljava/util/Map;)V\n')
    return text[:start] + body.replace(anchor, anchor+addition) + text[end:]


def helper_entry(original):
    info = zipfile.ZipInfo('classes12.dex', original.date_time)
    for attribute in ['compress_type', 'create_system', 'external_attr', 'internal_attr']:
        setattr(info, attribute, getattr(original, attribute))
    return info


def prepare(apk, android, out, work):
    if sha(apk.read_bytes()) != INPUT_SHA:
        raise ValueError('unreviewed Gallery predecessor')
    if work.exists() or out.exists():
        raise ValueError('refusing to overwrite candidate')
    work.mkdir(parents=True)
    java = android/'prebuilts/jdk/jdk21/linux-x86/bin'
    smali = android/'prebuilts/extract-tools/common/smali'
    sdk = android/'prebuilts/sdk/31/public/android.jar'
    sequence = 0

    def run(args):
        nonlocal sequence
        sequence += 1
        result = subprocess.run(list(map(str, args)), capture_output=True, timeout=180)
        (work/f'command-{sequence}.txt').write_bytes(result.stdout+result.stderr)
        result.check_returncode()

    with zipfile.ZipFile(apk) as archive:
        dex_names = {n for n in archive.namelist() if re.fullmatch(r'classes\d*\.dex', n)}
        if 'classes11.dex' not in dex_names or 'classes12.dex' in dex_names:
            raise ValueError('unexpected predecessor multidex layout')
        all_names = {name for member in dex_names for name, _ in definitions(archive.read(member))}
        if HELPER in all_names:
            raise ValueError('existing helper must not be replaced')
        old = archive.read('classes6.dex')
    selected = work/'request.dex'
    selected.write_bytes(select_definitions(old, {TARGET}))
    run([java/'java', '-Xmx512m', '-jar', smali/'baksmali.jar', 'disassemble',
         '--api', '31', '--output', work/'smali', selected])
    path = work/'smali'/Path(TARGET[1:-1]+'.smali')
    before = path.read_text()
    patched = patch_request(before)
    (work/'request-before.smali').write_text(before)
    path.write_text(patched)
    adapter = work/'adapter.dex'
    run([java/'java', '-Xmx512m', '-jar', smali/'smali.jar', 'assemble', '--api', '31',
         '--output', adapter, work/'smali'])
    classes = work/'classes'
    classes.mkdir()
    run([java/'javac', '--release', '8', '-cp', sdk, '-d', classes, SOURCE])
    dest = work/'helpers'
    dest.mkdir()
    run([java/'java', '-Xmx1g', '-cp', android/'prebuilts/r8/r8.jar',
         'com.android.tools.r8.D8', '--lib', sdk, '--min-api', '31', '--output', dest,
         *sorted(classes.rglob('*.class'))])
    if {n for n, _ in definitions((dest/'classes.dex').read_bytes())} != {HELPER}:
        raise ValueError('unexpected helper class set')
    # Adding a type reference to the almost-full original string pool promotes an
    # unrelated const-string to jumbo. Relocate only the reviewed request class;
    # keep the strict unaffected-class check instead of normalizing that change.
    host = work/'host'
    host.mkdir()
    library = ':'.join(str(p) for p in [*verified_paths(), smali/'baksmali.jar'])
    run([java/'javac', '--release', '17', '-cp', library, '-d', host,
         ROOT/'tools/oem_gallery_compat/GalleryDexRelocate.java',
         ROOT/'tools/oem_gallery_compat/GalleryDexSemanticCheck.java'])
    # Relocate from the full original pool, not the extraction intermediate.
    (work/'original.dex').write_bytes(old)
    run([java/'java', '-Xmx1g', '-cp', str(host)+':'+library, 'GalleryDexRelocate',
         work/'original.dex', work/'remaining.dex', work/'moved.dex', TARGET,
         adapter, dest/'classes.dex', HELPER])
    new = (work/'remaining.dex').read_bytes()
    moved = (work/'moved.dex').read_bytes()
    original_names = {n for n, _ in definitions(old)}
    if ({n for n, _ in definitions(new)} != original_names-{TARGET}
            or {n for n, _ in definitions(moved)} != {TARGET, HELPER}):
        raise ValueError('relocation changed class ownership')
    (work/'before-remaining.dex').write_bytes(select_definitions(old, original_names-{TARGET}))
    compare_cp = str(host)+':'+str(smali/'baksmali.jar')
    run([java/'java', '-Xmx1g', '-cp', compare_cp, 'GalleryDexSemanticCheck',
         work/'before-remaining.dex', work/'remaining.dex', work/'semantic-diff',
         '--normalize-defaults'])
    comparison = json.loads((work/f'command-{sequence}.txt').read_text())
    if not comparison['matching'] or comparison['classes'] != len(original_names)-1:
        raise ValueError('unaffected original class bodies/metadata differ')
    (work/'final-target.dex').write_bytes(select_definitions(moved, {TARGET}))
    run([java/'java', '-Xmx512m', '-jar', smali/'baksmali.jar', 'disassemble',
         '--api', '31', '--output', work/'final-target', work/'final-target.dex'])
    final = (work/'final-target'/Path(TARGET[1:-1]+'.smali')).read_text()
    a, b, original_constructor = method(before)
    c, d, final_constructor = method(final)
    if before[:a]+before[b:] != final[:c]+final[d:]:
        raise ValueError('other target methods or metadata differ')
    if final_constructor != method(patched)[2]:
        raise ValueError('constructor differs from reviewed insertion')
    with zipfile.ZipFile(apk) as source, zipfile.ZipFile(out, 'x') as target:
        for info in source.infolist():
            name = info.filename.upper()
            if name.startswith('META-INF/') and (name.endswith(('.SF', '.RSA', '.DSA', '.EC'))
                                                   or name == 'META-INF/MANIFEST.MF'):
                continue
            target.writestr(info, new if info.filename == 'classes6.dex' else source.read(info.filename))
        target.writestr(helper_entry(source.getinfo('classes6.dex')), moved)
    report = {'input_sha256': INPUT_SHA, 'unsigned_apk_sha256': sha(out.read_bytes()),
              'helper_source_sha256': sha(SOURCE.read_bytes()), 'semantic_comparison': comparison,
              'other_target_methods_and_metadata_exact': True,
              'constructor_exact_reviewed_insertion': True, 'added_dex': 'classes12.dex',
              'relocated_class': TARGET,
              'scope': 'Unsigned alpha9 follow-up: RMX3301-only, missing-only OEM model compatibility header. '
                       'Preserves OEM-provided values, Android version, UUID/DUID/signature inputs, '
                       'all other APK members and prior bundled corrections. No device/global property '
                       'or policy change, model execution or working Remover claim.'}
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ['apk', 'android', 'out', 'work']:
        parser.add_argument('--'+key, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.apk, args.android, args.out, args.work)))
