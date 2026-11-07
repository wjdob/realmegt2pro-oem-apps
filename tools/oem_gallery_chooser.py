"""Prepare version-bound optional Gallery Share labels and MIME handling, offline."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from oem_gallery_dex import merge_class
from oem_gallery_engine import replace_method
from oem_sdk import definitions, select_definitions
from bootstrap_gallery_dex import verified_paths

INPUT_SHA = '4433dda072631a1f58b6407dd99be3cc83c867e86ba02c3cc7658047506b24f0'
TYPE = 'Lcom/oplus/aiunit/vision/sb8;'
HELPER = 'Lrealme/gallery/compat/ResolverFallback;'
METHOD = '.method public a(Landroid/content/Intent;Landroid/content/pm/ResolveInfo;Ljava/lang/String;)Lcom/oplus/aiunit/vision/ht;'
SHARE = 'Lcom/oplus/gallery/business_lib/util/ShareUtils;'
MIME = 'Lrealme/gallery/compat/SingleShareMime;'
POLICY = 'Lrealme/gallery/compat/SingleShareMimePolicy;'
ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch_labels(text):
    """Supply the model's visible app text; leave its optional sublabel absent."""
    def transform(body):
        call = '    invoke-static {v1, p2}, ' + HELPER + '->label(Landroid/content/Context;Landroid/content/pm/ResolveInfo;)Ljava/lang/String;'
        old = call + '\n\n    move-result-object v1\n\n    const-string v2, ""'
        if (body.count(old) != 1 or body.count('    .registers 15') != 1
                or body.count('    move-object v4, p3') != 3
                or body.count('    move-object v5, p2') != 3
                or body.count('    return-object v0') != 1):
            raise ValueError('unreviewed resolver fallback or choice identity')
        new = call + '\n\n    move-result-object v2\n\n    const/4 v1, 0x0'
        return body.replace(old, new)
    return replace_method(text, METHOD, transform)


def patch_share_mime(text):
    method = '.method public static final j(Ljava/util/Set;Z)Landroid/content/Intent;'
    def transform(body):
        anchor = '    invoke-virtual {v2, p1}, Landroid/content/Intent;->setType(Ljava/lang/String;)Landroid/content/Intent;'
        read = '    invoke-virtual {v2, v0}, Landroid/content/Intent;->addFlags(I)Landroid/content/Intent;'
        if (body.count(anchor) != 1 or body.count('    .registers 16') != 1
                or body.count(read) != 1 or MIME in body or body.index(anchor) > body.index(read)):
            raise ValueError('unreviewed MIME setup or read-grant order')
        addition = ('\n\n    sget-object p1, Lcom/oplus/aiunit/vision/n33;->a:Landroid/content/Context;'
                    '\n\n    invoke-static {p1, v2}, ' + MIME
                    + '->refine(Landroid/content/Context;Landroid/content/Intent;)Landroid/content/Intent;'
                    '\n\n    move-result-object v2')
        return body.replace(anchor, anchor + addition)
    text = replace_method(text, method, transform)
    update = '.method public static final o(Landroid/content/Intent;Ljava/util/Set;Ljava/util/Map;)V'
    def refresh(body):
        anchor = '    invoke-virtual {p0, p1, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/io/Serializable;)Landroid/content/Intent;'
        if (body.count(anchor) != 1 or body.count('    .registers 13') != 1
                or MIME in body or body.count('    new-instance p0, Ljava/lang/StringBuilder;') != 1
                or body.index(anchor) > body.index('    new-instance p0, Ljava/lang/StringBuilder;')):
            raise ValueError('unreviewed converted-stream update')
        addition = ('\n\n    sget-object p1, Lcom/oplus/aiunit/vision/n33;->a:Landroid/content/Context;'
                    '\n\n    invoke-static {p1, p0}, ' + MIME
                    + '->refresh(Landroid/content/Context;Landroid/content/Intent;)V')
        return body.replace(anchor, anchor + addition)
    return replace_method(text, update, refresh)


def prepare_mime(old, android, work, run):
    work.mkdir(exist_ok=False)
    jdk = android/'prebuilts/jdk/jdk21/linux-x86/bin'
    smali = android/'prebuilts/extract-tools/common/smali'
    sdk = android/'prebuilts/sdk/31/public/android.jar'
    original_names = {name for name, _ in definitions(old)}
    if SHARE not in original_names or {MIME, POLICY} & original_names:
        raise ValueError('unexpected Share/MIME class ownership')
    selected = work/'selected.dex'
    selected.write_bytes(select_definitions(old, {SHARE}))
    stage = work/'smali'
    run([jdk/'java', '-Xmx512m', '-jar', smali/'baksmali.jar', 'disassemble',
         '--api', '31', '--output', stage, selected])
    source = stage/(SHARE[1:-1]+'.smali')
    before = source.read_text()
    (work/'share-before.smali').write_text(before)
    source.write_text(patch_share_mime(before))
    adapter = work/'adapter.dex'
    run([jdk/'java', '-Xmx512m', '-jar', smali/'smali.jar', 'assemble',
         '--api', '31', '--output', adapter, stage])
    classes = work/'classes'; classes.mkdir()
    sources = [ROOT/'tools/oem_gallery_compat'/(name+'.java')
               for name in ('SingleShareMime', 'SingleShareMimePolicy')]
    run([jdk/'javac', '--release', '8', '-cp', sdk, '-d', classes, *sources])
    helpers = work/'helpers'; helpers.mkdir()
    run([jdk/'java', '-Xmx512m', '-cp', android/'prebuilts/r8/r8.jar',
         'com.android.tools.r8.D8', '--lib', sdk, '--min-api', '31', '--output', helpers,
         *sorted(classes.rglob('*.class'))])
    helper_dex = helpers/'classes.dex'
    if {name for name, _ in definitions(helper_dex.read_bytes())} != {MIME, POLICY}:
        raise ValueError('unexpected helper closure')
    host = work/'host'; host.mkdir()
    library = ':'.join(map(str, [*verified_paths(), smali/'baksmali.jar']))
    run([jdk/'javac', '--release', '17', '-cp', library, '-d', host,
         ROOT/'tools/oem_gallery_compat/GalleryDexMediaAssembly.java',
         ROOT/'tools/oem_gallery_compat/GalleryDexSemanticCheck.java'])
    writer = str(host)+':'+library
    checker = str(host)+':'+str(smali/'baksmali.jar')
    original = work/'original.dex'; original.write_bytes(old)
    remaining = work/'remaining.dex'
    run([jdk/'java', '-Xmx1g', '-cp', writer, 'GalleryDexMediaAssembly',
         'remove', original, remaining, SHARE])
    original_remaining = work/'original-remaining.dex'
    original_remaining.write_bytes(select_definitions(old, original_names-{SHARE}))
    comparison = json.loads(run([jdk/'java', '-Xmx1g', '-cp', checker, 'GalleryDexSemanticCheck',
        original_remaining, remaining, work/'remaining-diff', '--normalize-defaults']).stdout)
    if not comparison['matching'] or comparison['classes'] != len(original_names)-1:
        raise ValueError('unrelated Share DEX definitions changed')
    moved = work/'moved.dex'
    run([jdk/'java', '-Xmx512m', '-cp', writer, 'GalleryDexMediaAssembly',
         'join', adapter, moved, helper_dex, SHARE])
    if {name for name, _ in definitions(moved.read_bytes())} != {SHARE, MIME, POLICY}:
        raise ValueError('relocated MIME class ownership differs')
    target = work/'final-target.dex'
    target.write_bytes(select_definitions(moved.read_bytes(), {SHARE}))
    target_comparison = json.loads(run([jdk/'java', '-Xmx512m', '-cp', checker,
        'GalleryDexSemanticCheck', adapter, target, work/'adapter-diff']).stdout)
    if not target_comparison['matching'] or target_comparison['classes'] != 1:
        raise ValueError('MIME adapter changed during relocation')
    return remaining.read_bytes(), moved.read_bytes(), {
        'unchanged_class_comparison': comparison, 'target_adapter_comparison': target_comparison,
        'sources': {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in sources}}


def prepare(apk, android, output, work):
    if not apk.is_file() or apk.is_symlink():
        raise ValueError('regular saved Gallery APK required')
    with apk.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != INPUT_SHA:
            raise ValueError('unreviewed Gallery input')
    if output.exists() or work.exists():
        raise ValueError('refusing to overwrite candidate or evidence')
    work.mkdir(parents=True)
    java = android / 'prebuilts/jdk/jdk21/linux-x86/bin/java'
    smali = android / 'prebuilts/extract-tools/common/smali'
    with zipfile.ZipFile(apk) as archive:
        if 'classes17.dex' in archive.namelist():
            raise ValueError('MIME helper DEX already owned')
        old = archive.read('classes7.dex')
        old_share = archive.read('classes5.dex')
    selected = work / 'selected.dex'
    selected.write_bytes(select_definitions(old, {TYPE}))
    commands = []
    def run(args):
        p = subprocess.run(list(map(str, args)), capture_output=True, timeout=180)
        commands.append({'args': list(map(str, args)), 'exit_code': p.returncode,
                         'stderr': p.stderr.decode(errors='replace')})
        (work / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        p.check_returncode()
        return p
    stage = work / 'smali'
    run([java, '-Xmx512m', '-jar', smali/'baksmali.jar', 'disassemble',
         '--api', '31', '--output', stage, selected])
    source = stage / (TYPE[1:-1] + '.smali')
    before = source.read_text()
    (work / 'resolver-before.smali').write_text(before)
    changed = patch_labels(before)
    source.write_text(changed)
    adapter = work / 'adapter.dex'
    run([java, '-Xmx512m', '-jar', smali/'smali.jar', 'assemble',
         '--api', '31', '--output', adapter, stage])
    new, semantic = merge_class(old, adapter, TYPE, android, work/'merge')
    final_target = work/'final-target.dex'
    final_target.write_bytes(select_definitions(new, {TYPE}))
    checked = run([java, '-Xmx512m', '-cp', str(work/'merge/host')+':'+str(smali/'baksmali.jar'),
                   'GalleryDexSemanticCheck', adapter, final_target, work/'adapter-comparison'])
    target_comparison = json.loads(checked.stdout)
    if not target_comparison['matching'] or target_comparison['classes'] != 1:
        raise ValueError('reviewed target changed during serialization')
    share_remaining, share_moved, mime_report = prepare_mime(old_share, android, work/'mime', run)
    retained = []
    with zipfile.ZipFile(apk) as original, zipfile.ZipFile(output, 'x') as candidate:
        for info in original.infolist():
            name = info.filename.upper()
            if name.startswith('META-INF/') and (name.endswith(('.SF', '.RSA', '.DSA', '.EC'))
                                                  or name == 'META-INF/MANIFEST.MF'):
                continue
            data = (new if info.filename == 'classes7.dex' else share_remaining
                    if info.filename == 'classes5.dex' else original.read(info.filename))
            candidate.writestr(info, data)
            if info.filename not in ('classes7.dex', 'classes5.dex'):
                retained.append(info.filename)
        predecessor = original.getinfo('classes5.dex')
        info = zipfile.ZipInfo('classes17.dex', predecessor.date_time)
        for key in ('compress_type', 'create_system', 'external_attr', 'internal_attr'):
            setattr(info, key, getattr(predecessor, key))
        candidate.writestr(info, share_moved)
    with zipfile.ZipFile(apk) as original, zipfile.ZipFile(output) as candidate:
        for name in retained:
            if original.read(name) != candidate.read(name):
                raise ValueError('unrelated APK member changed: ' + name)
    with output.open('rb') as stream:
        output_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    result = {'input_sha256': INPUT_SHA, 'unsigned_apk_sha256': output_sha,
              'changed_dex': 'classes7.dex', 'changed_class': TYPE,
              'before_smali_sha256': sha(before.encode()), 'after_smali_sha256': sha(changed.encode()),
              'unchanged_class_comparison': semantic, 'other_members_exact': len(retained),
              'target_adapter_comparison': target_comparison,
              'single_mime': mime_report,
              'tool_sha256': sha(Path(__file__).read_bytes()),
              'scope': 'Unsigned cumulative Share-label and single-media MIME candidate. No package-name deduplication. Target ResolveInfo/tag, manifest, all other class definitions/resources/native code and media permissions remain exact. MIME metadata refines a copied single content-URI SEND intent; after OEM privacy/format conversion it refreshes the same intent from its final URI. Multiple/unknown/failing metadata retains original handling. No signing, installation or UI acceptance.'}
    (work/'report.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('apk', 'android', 'output', 'work'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.apk, args.android, args.output, args.work), indent=2))
