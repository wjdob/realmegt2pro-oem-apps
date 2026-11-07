"""Prepare an unsigned, version-bound album-move consent prototype; no device action.

This deliberately does not rebuild or replace the signed Gallery-alpha8 archives.
The worker batch boundary is not an atomic boundary for an entire multi-batch move.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from oem_sdk import definitions, select_definitions
from oem_gallery_dex import merge_class

ROOT = Path(__file__).resolve().parents[1]
INPUT_SHA = 'e67025a698ce76f200328ae01fbaa252cbae5ebd1f7052cee6a8b80d5387289a'
TARGET = 'Lcom/oplus/aiunit/vision/ad9$c$a;'
HELPER = 'Lrealme/gallery/compat/MoveWriteConsent;'
METHOD = '.method public final invoke(Ljava/util/ArrayList;)V'
SOURCES = [ROOT/'tools/oem_gallery_compat'/name for name in
           ['MoveWriteConsent.java', 'MoveMediaItem.java', 'MoveConsentState.java']]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def helper_entry(predecessor):
    """Anchor new-member metadata to the input, never the host build clock."""
    info = zipfile.ZipInfo('classes11.dex', predecessor.date_time)
    info.compress_type = predecessor.compress_type
    info.create_system = predecessor.create_system
    info.external_attr = predecessor.external_attr
    info.internal_attr = predecessor.internal_attr
    return info


def patch_move_callback(text):
    if text.count(METHOD) != 1 or HELPER in text or ':realme_move_original' in text:
        raise ValueError('unexpected move callback preimage')
    start = text.index(METHOD)
    end = text.index('.end method', start) + len('.end method')
    body = text[start:end]
    needle = ('    invoke-static {}, Lcom/oplus/aiunit/vision/r45;'
              '->a()Lcom/oplus/aiunit/vision/r45;')
    for marker in ['    .registers 13', needle, '    monitor-enter v4',
                   '    iget-object v2, p0, '+TARGET+'->this$0:Lcom/oplus/aiunit/vision/ad9;',
                   '    iget-object v3, p0, '+TARGET+'->$context:Landroid/content/Context;']:
        if body.count(marker) != 1:
            raise ValueError('unexpected move callback layout')
    if not body.index('->$context:') < body.index(needle) < body.index('    monitor-enter v4'):
        raise ValueError('move gate would occur after mutation lock')
    gate = ('    instance-of v4, v2, Lcom/oplus/aiunit/vision/md9;\n\n'
            '    if-eqz v4, :realme_move_original\n\n'
            '    invoke-static {v3, v2, p1}, '+HELPER+
            '->authorize(Landroid/content/Context;Ljava/lang/Object;Ljava/util/List;)Z\n\n'
            '    move-result v4\n\n'
            '    if-nez v4, :realme_move_original\n\n'
            '    const/4 v4, 0x1\n\n'
            '    iput-boolean v4, v2, Lcom/oplus/aiunit/vision/ad9;->m:Z\n\n'
            '    return-void\n\n'
            '    :realme_move_original\n')
    return text[:start] + body.replace(needle, gate+needle) + text[end:]


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

    with zipfile.ZipFile(apk) as z:
        old = z.read('classes5.dex')
    selected = work/'callback.dex'
    selected.write_bytes(select_definitions(old, {TARGET}))
    run([java/'java', '-Xmx512m', '-jar', smali/'baksmali.jar', 'disassemble',
         '--api', '31', '--output', work/'smali', selected])
    path = work/'smali'/Path(TARGET[1:-1]+'.smali')
    before = path.read_text()
    (work/'callback-before.smali').write_text(before)
    path.write_text(patch_move_callback(before))
    adapter = work/'adapter.dex'
    run([java/'java', '-Xmx512m', '-jar', smali/'smali.jar', 'assemble', '--api', '31',
         '--output', adapter, work/'smali'])
    classes = work/'classes'
    classes.mkdir()
    run([java/'javac', '--release', '8', '-cp', sdk, '-d', classes, *SOURCES])
    dest = work/'helpers'
    dest.mkdir()
    run([java/'java', '-Xmx1g', '-cp', android/'prebuilts/r8/r8.jar',
         'com.android.tools.r8.D8', '--lib', sdk, '--min-api', '31', '--output', dest,
         *sorted(classes.rglob('*.class'))])
    if sorted(p.name for p in dest.iterdir()) != ['classes.dex']:
        raise ValueError('unexpected helper DEX split')
    helper_names = {n for n, _ in definitions((dest/'classes.dex').read_bytes())}
    if not helper_names or any(not n.startswith('Lrealme/gallery/compat/Move') for n in helper_names):
        raise ValueError('unexpected helper class')
    # Keep new helpers outside the almost-full original string pool. Rewriting
    # unrelated OEM methods to accommodate helper strings would widen this patch.
    with zipfile.ZipFile(apk) as source:
        if 'classes11.dex' in source.namelist():
            raise ValueError('unexpected predecessor multidex layout')
        original_names = {n for member in source.namelist() if member.endswith('.dex')
                          for n, _ in definitions(source.read(member))}
    if helper_names & original_names:
        raise ValueError('helper shadows an existing class')
    new, comparison = merge_class(old, adapter, TARGET, android, work/'merge')
    with zipfile.ZipFile(apk) as source, zipfile.ZipFile(out, 'x') as target:
        for info in source.infolist():
            name = info.filename.upper()
            if name.startswith('META-INF/') and (name.endswith(('.SF', '.RSA', '.DSA', '.EC'))
                                                   or name == 'META-INF/MANIFEST.MF'):
                continue
            target.writestr(info, new if info.filename == 'classes5.dex' else source.read(info.filename))
        target.writestr(helper_entry(source.getinfo('classes5.dex')), (dest/'classes.dex').read_bytes())
    report = dict(input_sha256=INPUT_SHA, unsigned_apk_sha256=sha(out.read_bytes()),
                  callback_preimage_sha256=sha(before.encode()), semantic_comparison=comparison,
                  added_dex='classes11.dex', helper_classes=sorted(helper_names),
                  source_sha256={p.name: sha(p.read_bytes()) for p in SOURCES},
                  scope='Unsigned experimental md9 local-image/video batch consent gate. No media mutation, '
                        'broad grant, exported component, manifest/native/resource change or installed acceptance. '
                        'Earlier completed batches are not rolled back if a later batch is cancelled. '
                        'Cross-volume/cloud/unknown records fail before this batch starts. '
                        'A controlled-trial package may be prepared offline; production acceptance requires '
                        'manual approval/cancel/rotation/ownership-change and actual provider/move checks.')
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ['apk', 'android', 'out', 'work']:
        parser.add_argument('--'+key, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.apk, args.android, args.out, args.work)))
