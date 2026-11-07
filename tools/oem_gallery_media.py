"""Prepare a pinned optional-Gallery media-management candidate; no device action.

Keep base Lineage roles/policy unchanged. Add the standard media-management
declaration and shared requests for Move and pre-save Replace original access.
The output is unsigned; host preservation/compilation is not UI or save acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import zipfile

from bootstrap_gallery_dex import verified_paths
from oem_gallery_app import ANDROID, append_string, string_pool, xml_chunks
from oem_gallery_engine import replace_method
from oem_sdk import definitions, select_definitions

ROOT = Path(__file__).resolve().parents[1]
INPUT_SHA = '2dc4a37111a17513343ab235e0d9cd55ec2159782843a09441a6773584d91868'
MEDIA = 'Lrealme/gallery/compat/MediaWriteConsent;'
REPLACE = 'Lrealme/gallery/compat/ReplaceOriginalAccess;'
APP = 'Lcom/oplus/gallery/framework/app/GalleryApplication;'
WORKER = 'Lcom/oplus/aiunit/vision/vga;'
QUERY = 'Lcom/oplus/aiunit/vision/nqc;'
MOVE = 'Lrealme/gallery/compat/MoveWriteConsent$ConsentFragment;'
TARGETS = {'classes6.dex': {APP, WORKER}, 'classes7.dex': {QUERY}, 'classes11.dex': {MOVE}}
PERMISSION = 'android.permission.MANAGE_MEDIA'
NONE = 0xffffffff
SOURCES = [ROOT / 'tools/oem_gallery_compat' / (name + '.java') for name in
           ['MediaConsentFlow', 'MediaWriteConsent', 'MediaSaveScope', 'ReplaceMediaItem', 'ReplaceOriginalAccess']]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def add_media_management(data):
    chunks = xml_chunks(data)
    pools = [i for i, (kind, _) in enumerate(chunks) if kind == 1]
    if len(pools) != 1:
        raise ValueError('one manifest string pool required')
    pi = pools[0]
    pool = chunks[pi][1]
    original = string_pool(pool)[0]
    for value in ['uses-permission', 'name', ANDROID, PERMISSION]:
        if value not in string_pool(pool)[0]:
            pool, _ = append_string(pool, value)
    strings = string_pool(pool)[0]
    ids = {value: i for i, value in enumerate(strings)}
    maps = [raw for kind, raw in chunks if kind == 0x180]
    if len(maps) != 1:
        raise ValueError('one manifest resource map required')
    mapping = struct.unpack('<' + 'I' * ((len(maps[0]) - 8) // 4), maps[0][8:])
    if ids['name'] >= len(mapping) or mapping[ids['name']] != 0x01010003:
        raise ValueError('name attribute resource identity changed')
    stack = []
    position = None
    for i, (kind, raw) in enumerate(chunks):
        if kind not in (0x102, 0x103):
            continue
        ni = struct.unpack_from('<I', raw, 20)[0]
        if ni >= len(original):
            raise ValueError('unreviewed XML node')
        name = original[ni]
        if kind == 0x102:
            if name in ('uses-permission', 'uses-permission-sdk-23') and stack == ['manifest']:
                count = struct.unpack_from('<H', raw, 28)[0]
                for a in range(count):
                    ns, key, value, size, zero, typ, typed = struct.unpack_from('<IIIHBBI', raw, 36 + 20 * a)
                    if key == ids['name'] and typ == 3 and strings[typed] == PERMISSION:
                        raise ValueError('media management already declared')
            if name == 'application' and stack == ['manifest']:
                if position is not None:
                    raise ValueError('multiple applications')
                position = i
            stack.append(name)
        elif not stack or stack.pop() != name:
            raise ValueError('unbalanced manifest')
    if stack or position is None:
        raise ValueError('missing application')
    attr = struct.pack('<IIIHBBI', ids[ANDROID], ids['name'], ids[PERMISSION], 8, 0, 3, ids[PERMISSION])
    entry = (struct.pack('<HHIII', 0x102, 16, 56, 0, NONE)
             + struct.pack('<II6H', NONE, ids['uses-permission'], 20, 20, 1, 0, 0, 0) + attr
             + struct.pack('<HHIIIII', 0x103, 16, 24, 0, NONE, NONE, ids['uses-permission']))
    chunks[pi] = (1, pool)
    body = b''.join(raw for _, raw in chunks[:position]) + entry + b''.join(raw for _, raw in chunks[position:])
    result = struct.pack('<HHI', 3, 8, 8 + len(body)) + body
    xml_chunks(result)
    return result


def patch_application(text):
    if MEDIA in text:
        raise ValueError('application tracker already present')
    def patch(body):
        anchor = '    invoke-super {p0, p1}, Landroid/app/Application;->attachBaseContext(Landroid/content/Context;)V'
        if body.count(anchor) != 1 or body.count('    .registers 2') != 1:
            raise ValueError('unreviewed application bootstrap')
        return body.replace(anchor, anchor + '\n\n    invoke-static {p0}, ' + MEDIA + '->install(Landroid/app/Application;)V')
    return replace_method(text, '.method public attachBaseContext(Landroid/content/Context;)V', patch)


def patch_worker(text):
    if REPLACE in text:
        raise ValueError('save scope already attached')
    def patch(body):
        anchor = '    sget-object v4, Lkotlin/Result;->Companion:Lkotlin/Result$Companion;'
        if (body.count(anchor) != 1 or body.count('    .registers 8') != 1
                or body.count('->run()V') != 1 or body.count('    :try_start_16') != 1
                or body.index('    :try_start_16') > body.index(anchor)):
            raise ValueError('unreviewed IO save worker')
        addition = ('\n\n    iget-object v4, p0, ' + WORKER + '->L$0:Ljava/lang/Object;\n\n'
                    '    invoke-static {v0, v4}, ' + REPLACE + '->attach(Ljava/lang/Object;Ljava/lang/Object;)V')
        return body.replace(anchor, anchor + addition)
    return replace_method(text, '.method public final invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;', patch)


def patch_query(text):
    if REPLACE in text:
        raise ValueError('source-query gate already present')
    def patch(body):
        if (body.count('    .registers 10') != 1 or len(re.findall(r'\bv9\b', body)) != 0
                or body.count('    return-void') != 2 or body.count('    :goto_b1') != 1
                or 'Lcom/oplus/aiunit/vision/k9b;->a(Landroid/database/Cursor;)V' not in body):
            raise ValueError('unreviewed original-file query')
        body = body.replace('    .registers 10', '    .registers 11\n\n    iget-object v9, p0, ' + QUERY + '->a:Lcom/oplus/aiunit/vision/fid;')
        return body.replace('    return-void', '    invoke-static {v9}, ' + REPLACE + '->authorize(Ljava/lang/Object;)V\n\n    return-void')
    return replace_method(text, '.method public run()V', patch)


def patch_move_fragment(text):
    if MEDIA in text:
        raise ValueError('shared media request already present')
    def resume(body):
        if 'Landroid/provider/MediaStore;->createWriteRequest(' not in body or body.count('    .registers 11') != 1:
            raise ValueError('unreviewed Move consent entry')
        return ('.method public onResume()V\n    .registers 4\n\n'
                '    invoke-super {p0}, Landroid/app/Fragment;->onResume()V\n\n'
                '    iget-object v0, p0, ' + MOVE + '->state:Lrealme/gallery/compat/MoveConsentState;\n\n'
                '    iget-object v1, p0, ' + MOVE + '->requests:Ljava/util/ArrayList;\n\n'
                '    const/16 v2, 0x4d37\n\n'
                '    invoke-static {p0, v0, v1, v2}, ' + MEDIA + '->begin(Landroid/app/Fragment;Lrealme/gallery/compat/MoveConsentState;Ljava/util/ArrayList;I)V\n\n'
                '    return-void\n.end method')
    text = replace_method(text, '.method public onResume()V', resume)
    def result(body):
        anchor = '    invoke-super {p0, p1, p2, p3}, Landroid/app/Fragment;->onActivityResult(IILandroid/content/Intent;)V'
        if (body.count(anchor) != 1 or body.count('    .registers 4') != 1
                or re.search(r'\bv0\b', body)):
            raise ValueError('unreviewed Move consent callback')
        added = ('\n\n    iget-object v0, p0, ' + MOVE + '->state:Lrealme/gallery/compat/MoveConsentState;\n\n'
                 '    invoke-static {p0, v0, p1, p2}, ' + MEDIA + '->result(Landroid/app/Fragment;Lrealme/gallery/compat/MoveConsentState;II)Z\n\n'
                 '    move-result v0\n\n    if-eqz v0, :realme_media_original_result\n\n'
                 '    return-void\n\n    :realme_media_original_result')
        return body.replace('    .registers 4', '    .registers 5').replace(anchor, anchor + added)
    text = replace_method(text, '.method public onActivityResult(IILandroid/content/Intent;)V', result)
    def destroy(body):
        if body.count('    .registers 3') != 1:
            raise ValueError('unreviewed Move fragment destruction')
        return body.replace('    .registers 3', '    .registers 3\n\n    invoke-static {p0}, ' + MEDIA + '->destroy(Landroid/app/Fragment;)V')
    return replace_method(text, '.method public onDestroy()V', destroy)


def prepare(apk, android, output, work):
    if sha(apk.read_bytes()) != INPUT_SHA:
        raise ValueError('unreviewed Gallery predecessor')
    if output.exists() or work.exists():
        raise ValueError('refusing to overwrite candidate')
    work.mkdir(parents=True)
    java = android / 'prebuilts/jdk/jdk21/linux-x86/bin'
    smali = android / 'prebuilts/extract-tools/common/smali'
    sdk = android / 'prebuilts/sdk/31/public/android.jar'
    calls = 0
    def run(arguments):
        nonlocal calls
        calls += 1
        p = subprocess.run(list(map(str, arguments)), capture_output=True, timeout=180)
        (work / f'command-{calls}.txt').write_bytes(p.stdout + p.stderr)
        p.check_returncode()
        return p
    patches = {APP: patch_application, WORKER: patch_worker, QUERY: patch_query, MOVE: patch_move_fragment}
    with zipfile.ZipFile(apk) as source:
        if 'classes16.dex' in source.namelist() or not set(TARGETS) <= set(source.namelist()):
            raise ValueError('unreviewed multidex layout')
        old = {name: source.read(name) for name in TARGETS}
        all_names = {name for member in source.namelist() if member.startswith('classes') and member.endswith('.dex')
                     for name, _ in definitions(source.read(member))}
        manifest = add_media_management(source.read('AndroidManifest.xml'))
    adapter_dir = work / 'adapters'
    adapter_dir.mkdir()
    original_names = {}
    for dex, targets in TARGETS.items():
        original_names[dex] = {name for name, _ in definitions(old[dex])}
        if not targets <= original_names[dex]:
            raise ValueError('adapter ownership changed')
        original = work / dex
        original.write_bytes(old[dex])
        selected = work / (dex + '-selected.dex')
        selected.write_bytes(select_definitions(old[dex], targets))
        stage = work / (dex + '-smali')
        run([java / 'java', '-Xmx512m', '-jar', smali / 'baksmali.jar', 'disassemble', '--api', '31', '--output', stage, selected])
        for target in sorted(targets):
            path = Path(target[1:-1] + '.smali')
            original_text = (stage / path).read_text()
            changed = patches[target](original_text)
            (stage / path).with_suffix('.before').write_text(original_text)
            destination = adapter_dir / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(changed)
    adapter = work / 'adapters.dex'
    run([java / 'java', '-Xmx512m', '-jar', smali / 'smali.jar', 'assemble', '--api', '31', '--output', adapter, adapter_dir])
    classes = work / 'classes'
    classes.mkdir()
    run([java / 'javac', '--release', '8', '-cp', sdk, '-d', classes,
         ROOT / 'tools/oem_gallery_compat/MoveConsentState.java', *SOURCES])
    helpers = work / 'helpers'
    helpers.mkdir()
    helper_classes = [p for p in sorted(classes.rglob('*.class')) if p.name != 'MoveConsentState.class']
    run([java / 'java', '-Xmx768m', '-cp', android / 'prebuilts/r8/r8.jar', 'com.android.tools.r8.D8',
         '--lib', sdk, '--classpath', classes, '--min-api', '31', '--output', helpers, *helper_classes])
    helper_names = {name for name, _ in definitions((helpers / 'classes.dex').read_bytes())}
    if (not helper_names or helper_names & all_names
            or any(not name.startswith(('Lrealme/gallery/compat/Media', 'Lrealme/gallery/compat/Replace')) for name in helper_names)):
        raise ValueError('unreviewed helper ownership')
    libraries = ':'.join(map(str, [*verified_paths(), smali / 'baksmali.jar']))
    host = work / 'host'
    host.mkdir()
    run([java / 'javac', '--release', '17', '-cp', libraries, '-d', host,
         ROOT / 'tools/oem_gallery_compat/GalleryDexMediaAssembly.java',
         ROOT / 'tools/oem_gallery_compat/GalleryDexSemanticCheck.java'])
    writer_cp = str(host) + ':' + libraries
    checker_cp = str(host) + ':' + str(smali / 'baksmali.jar')
    remaining = {}
    comparisons = {}
    for dex, targets in TARGETS.items():
        dest = work / (dex + '-remaining.dex')
        run([java / 'java', '-Xmx1g', '-cp', writer_cp, 'GalleryDexMediaAssembly', 'remove', work / dex, dest, *sorted(targets)])
        before = work / (dex + '-before-remaining.dex')
        before.write_bytes(select_definitions(old[dex], original_names[dex] - targets))
        p = run([java / 'java', '-Xmx1g', '-cp', checker_cp, 'GalleryDexSemanticCheck', before, dest,
                 work / (dex + '-remaining-diff'), '--normalize-defaults'])
        comparison = json.loads(p.stdout)
        if not comparison['matching'] or comparison['classes'] != len(original_names[dex]) - len(targets):
            raise ValueError('unaffected class bodies or metadata changed')
        remaining[dex] = dest.read_bytes()
        comparisons[dex] = comparison
    moved = work / 'moved.dex'
    target_names = set(patches)
    run([java / 'java', '-Xmx768m', '-cp', writer_cp, 'GalleryDexMediaAssembly', 'join', adapter, moved,
         helpers / 'classes.dex', *sorted(target_names)])
    moved_data = moved.read_bytes()
    if {name for name, _ in definitions(moved_data)} != target_names | helper_names:
        raise ValueError('combined class ownership differs')
    target_dex = work / 'final-adapters.dex'
    target_dex.write_bytes(select_definitions(moved_data, target_names))
    p = run([java / 'java', '-Xmx512m', '-cp', checker_cp, 'GalleryDexSemanticCheck', adapter, target_dex, work / 'adapter-diff'])
    adapter_comparison = json.loads(p.stdout)
    if not adapter_comparison['matching']:
        raise ValueError('reviewed adapters changed during relocation')
    with zipfile.ZipFile(apk) as source, zipfile.ZipFile(output, 'x') as target:
        for info in source.infolist():
            upper = info.filename.upper()
            if upper.startswith('META-INF/') and (upper.endswith(('.SF', '.RSA', '.DSA', '.EC')) or upper == 'META-INF/MANIFEST.MF'):
                continue
            data = manifest if info.filename == 'AndroidManifest.xml' else remaining.get(info.filename, source.read(info.filename))
            target.writestr(info, data)
        predecessor = source.getinfo('classes6.dex')
        info = zipfile.ZipInfo('classes16.dex', predecessor.date_time)
        for key in ['compress_type', 'create_system', 'external_attr', 'internal_attr']:
            setattr(info, key, getattr(predecessor, key))
        target.writestr(info, moved_data)
    report = {'input_sha256': INPUT_SHA, 'unsigned_apk_sha256': sha(output.read_bytes()),
              'manifest_sha256': sha(manifest), 'added_permission': PERMISSION,
              'moved_targets': sorted(target_names), 'new_helpers': sorted(helper_names),
              'remaining_comparisons': comparisons, 'adapter_comparison': adapter_comparison,
              'source_sha256': {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in SOURCES},
              'scope': 'Unsigned optional-Gallery candidate: standard media-management declaration, shared Move requests, IO-scope-bound original-media consent after source query and before save. Base roles/MediaProvider/signers/policy untouched; no automatic app-op assignment, all-files grant or media edit/delete. Actual permission handling, cancellation, repeated Move/Replace, saved output and prior-feature regressions need live acceptance.'}
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ['apk', 'android', 'output', 'work']:
        parser.add_argument('--' + key, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.apk, args.android, args.output, args.work)))
