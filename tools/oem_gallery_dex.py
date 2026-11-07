"""Merge one reviewed class without recompiling the unaffected OEM definitions."""
import json
from pathlib import Path
import subprocess
from oem_sdk import definitions,select_definitions
from oem_camera_window import abi
from bootstrap_gallery_dex import verified_paths

ROOT=Path(__file__).resolve().parents[1]


def merge_class(old,adapter,target,android,work,helpers=None):
    work.mkdir(exist_ok=False);java=android/'prebuilts/jdk/jdk21/linux-x86/bin'
    library=':'.join(str(p) for p in [*verified_paths(),android/'prebuilts/extract-tools/common/smali/baksmali.jar'])
    sequence=0
    def run(args):
        nonlocal sequence
        sequence+=1
        result=subprocess.run(list(map(str,args)),capture_output=True,timeout=180)
        (work/('command-'+str(sequence)+'.txt')).write_bytes(result.stdout+result.stderr)
        result.check_returncode();return result
    before=work/'original.dex';before.write_bytes(old);after=work/'merged.dex'
    host=work/'host';host.mkdir()
    run([java/'javac','--release','17','-cp',library,'-d',host,
         ROOT/'tools/oem_gallery_compat/GalleryDexMerge.java',
         ROOT/'tools/oem_gallery_compat/GalleryDexSemanticCheck.java'])
    classpath=str(host)+':'+str(library)
    args=[java/'java','-Xmx1g','-cp',classpath,'GalleryDexMerge',before,after,target,adapter]
    if helpers is not None:args.append(helpers)
    run(args);new=after.read_bytes();names={n for n,_ in definitions(old)}
    if target not in names or abi(old,{target})!=abi(new,{target}):raise ValueError('replacement ABI differs')
    added={n for n,_ in definitions(helpers.read_bytes())} if helpers is not None else set()
    if names & added or {n for n,_ in definitions(new)}!=names|added:raise ValueError('merged class set differs')
    for label,data in [('before',old),('after',new)]:
        (work/(label+'-remaining.dex')).write_bytes(select_definitions(data,names-{target}))
    # Android's stripped baksmali has its own matched formatter implementation;
    # do not shadow it with dexlib2's older utility class during disassembly.
    compare_classpath=str(host)+':'+str(android/'prebuilts/extract-tools/common/smali/baksmali.jar')
    value=run([java/'java','-Xmx1g','-cp',compare_classpath,'GalleryDexSemanticCheck',
               work/'before-remaining.dex',work/'after-remaining.dex',work/'semantic-diff','--normalize-defaults'])
    report=json.loads(value.stdout)
    if not report['matching'] or report['classes']!=len(names)-1:raise ValueError('original class bodies/metadata differ')
    report['added_classes']=sorted(added)
    report['comparison']='Original classes directly; only explicit-vs-omitted zero/null static initialization normalized. Other annotations, private members and method bodies retained.'
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');return new,report
