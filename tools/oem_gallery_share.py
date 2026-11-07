"""Share selected media with read access, preserving content/privacy choices."""
from pathlib import Path
import re
import subprocess
from oem_sdk import select_definitions
from oem_gallery_dex import merge_class

TYPE='Lcom/oplus/gallery/business_lib/util/ShareUtils;'


def patch_share_utils(text):
    marker='.method public static final j(Ljava/util/Set;Z)Landroid/content/Intent;'
    if text.count(marker)!=1:raise ValueError('unexpected share utility preimage')
    start=text.index(marker);end=text.index('.end method',start)+len('.end method');body=text[start:end]
    write_pattern=(r'    const/4 p0, 0x2\n\n    \.line 27\n(?:\n)?'
                   r'    invoke-virtual \{v2, p0\}, Landroid/content/Intent;->addFlags\(I\)Landroid/content/Intent;\n\n')
    matches=list(re.finditer(write_pattern,body))
    read='    invoke-virtual {v2, v0}, Landroid/content/Intent;->addFlags(I)Landroid/content/Intent;'
    if body.count('    .registers 16')!=1 or len(matches)!=1 or body.count(read)!=1:
        raise ValueError('unexpected share grant/register layout')
    match=matches[0]
    if body.index(read)>match.start():raise ValueError('unexpected read/write order')
    # SEND/SEND_MULTIPLE consumers need read access. Asking to delegate WRITE on
    # Camera-owned media can reject the entire handoff on standard MediaProvider.
    # No copied/stripped output is substituted and the existing read grant stays.
    return text[:start]+body[:match.start()]+body[match.end():]+text[end:]


def prepare_share_dex(old,android,work):
    work.mkdir(exist_ok=False);java=android/'prebuilts/jdk/jdk21/linux-x86/bin';smali=android/'prebuilts/extract-tools/common/smali'
    run=lambda args:subprocess.run(list(map(str,args)),check=True,capture_output=True,timeout=180)
    selected=work/'selected.dex';selected.write_bytes(select_definitions(old,{TYPE}))
    run([java/'java','-Xmx512m','-jar',smali/'baksmali.jar','disassemble','--api','31','--output',work/'smali',selected])
    path=work/'smali'/Path(TYPE[1:-1]+'.smali');before=path.read_text();(work/'before.smali').write_text(before);path.write_text(patch_share_utils(before))
    adapter=work/'adapter.dex';run([java/'java','-Xmx512m','-jar',smali/'smali.jar','assemble','--api','31','--output',adapter,work/'smali'])
    return merge_class(old,adapter,TYPE,android,work/'merge')
