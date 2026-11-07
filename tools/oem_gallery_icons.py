"""Version-bound resolver fallback; original OEM icon path remains intact."""
from pathlib import Path
import subprocess
from oem_sdk import definitions,select_definitions
from oem_gallery_dex import merge_class

TYPE='Lcom/oplus/aiunit/vision/sb8;'
HELPER='Lrealme/gallery/compat/ResolverFallback;'


def patch_icon_helper(text):
    marker='.method public a(Landroid/content/Intent;Landroid/content/pm/ResolveInfo;Ljava/lang/String;)Lcom/oplus/aiunit/vision/ht;'
    if text.count(marker)!=1 or HELPER in text:raise ValueError('unexpected resolver helper preimage')
    start=text.index(marker);end=text.index('.end method',start)+len('.end method');body=text[start:end]
    needle='    if-nez v1, :cond_22\n\n    return-object v2'
    if body.count(needle)!=1 or body.count('    .registers 15')!=1:raise ValueError('unexpected resolver branch/register layout')
    fallback='''    if-nez v1, :cond_22

    iget-object v1, p0, Lcom/oplus/aiunit/vision/sb8;->a:Landroid/content/Context;
    invoke-static {v1, p2}, Lrealme/gallery/compat/ResolverFallback;->icon(Landroid/content/Context;Landroid/content/pm/ResolveInfo;)Landroid/graphics/drawable/Drawable;
    move-result-object v3
    invoke-static {v1, p2}, Lrealme/gallery/compat/ResolverFallback;->label(Landroid/content/Context;Landroid/content/pm/ResolveInfo;)Ljava/lang/String;
    move-result-object v1
    const-string v2, ""
    move-object v4, p3
    move-object v5, p2
    const/4 v6, 0x0
    const/16 v7, 0x20
    new-instance v0, Lcom/oplus/aiunit/vision/ht;
    invoke-direct/range {v0 .. v7}, Lcom/oplus/aiunit/vision/ht;-><init>(Ljava/lang/String;Ljava/lang/String;Landroid/graphics/drawable/Drawable;Ljava/lang/String;Landroid/content/pm/ResolveInfo;ZI)V
    return-object v0'''
    return text[:start]+body.replace(needle,fallback)+text[end:]


def prepare_icon_dex(old,android,work):
    work.mkdir(exist_ok=False);java=android/'prebuilts/jdk/jdk21/linux-x86/bin';smali=android/'prebuilts/extract-tools/common/smali';sdk=android/'prebuilts/sdk/31/public/android.jar'
    names={n for n,_ in definitions(old)}
    if TYPE not in names:raise ValueError('missing resolver helper')
    run=lambda args:subprocess.run(list(map(str,args)),check=True,capture_output=True,timeout=180)
    selected=work/'selected.dex';selected.write_bytes(select_definitions(old,{TYPE}))
    run([java/'java','-Xmx512m','-jar',smali/'baksmali.jar','disassemble','--api','31','--output',work/'smali',selected])
    path=work/'smali'/Path(TYPE[1:-1]+'.smali');before=path.read_text();(work/'before.smali').write_text(before);path.write_text(patch_icon_helper(before))
    adapter=work/'adapter.dex';run([java/'java','-Xmx512m','-jar',smali/'smali.jar','assemble','--api','31','--output',adapter,work/'smali'])
    return merge_class(old,adapter,TYPE,android,work/'merge')
