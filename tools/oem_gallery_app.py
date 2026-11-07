"""Prepare additive, version-bound optional Gallery APK compatibility candidates.

No device operation, framework change, SDK/version bump, permission grant or key
creation. Inputs/output are private; use a dedicated add-on app signing identity.
The portable flavor retains the OEM service permission. The Lineage flavor uses
the existing signature/privileged SYSTEM_CAMERA gate on that one predecode service.
Neither a completed build nor a static audit proves application acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zipfile

from oem_sdk import definitions, select_definitions
from oem_camera_window import abi

ROOT = Path(__file__).resolve().parents[1]
APK_SHA = 'ce058185478efe7faed94ffbae42bc640741e33744963dcf1afd93f7f693c20e'
MANIFEST_SHA = 'd7114fbd87c56595405c205bfe70a0e91d4dd70f2e4623398dbd8f9e865f6ef9'
DEX_SHA = '29dbec44ef39619ad33b5a7ce9eb8b693bed8475360d4d27ae86af20a06dc378'
TYPE = 'Lcom/oplus/aiunit/vision/ft;'
SOURCE = ROOT/'tools/oem_gallery_compat/com/oplus/aiunit/vision/ft.java'
SERVICE = 'com.oplus.gallery.picture_page.predecode.OplusPreTileDecodeService'
OLD_PERMISSION = 'oppo.permission.OPPO_COMPONENT_SAFE'
NEW_PERMISSION = 'android.permission.SYSTEM_CAMERA'
ANDROID = 'http://schemas.android.com/apk/res/android'
FLAGS_PATCH = '''    # Additive Android14+ explicit receiver state; keep older APIs/explicit flags.
    sget v0, Landroid/os/Build$VERSION;->SDK_INT:I
    const/16 v1, 0x22
    if-lt v0, v1, :lineage_gallery_flags_original
    and-int/lit8 v0, p3, 0x7
    if-nez v0, :lineage_gallery_flags_original
    if-eqz p2, :lineage_gallery_flags_private
    invoke-virtual {p2}, Ljava/lang/String;->isEmpty()Z
    move-result v0
    if-nez v0, :lineage_gallery_flags_private
    or-int/lit8 p3, p3, 0x2
    goto :lineage_gallery_flags_original
    :lineage_gallery_flags_private
    or-int/lit8 p3, p3, 0x4
    :lineage_gallery_flags_original
'''


def patch_descriptor_smali(text):
    marker='.method public constructor <init>(Ljava/lang/String;Ljava/lang/String;I)V'
    if text.count(marker)!=1:
        raise ValueError('Unexpected descriptor constructor')
    start=text.index(marker);end=text.index('.end method',start)
    constructor=text[start:end]
    assignment='    iput p3, p0, Lcom/oplus/aiunit/vision/ft;->c:I'
    if constructor.count('    .locals 1')!=1 or constructor.count(assignment)!=1 or ':lineage_gallery_' in text:
        raise ValueError('Unexpected descriptor register/field layout')
    changed=constructor.replace('    .locals 1','    .locals 2',1).replace(assignment,FLAGS_PATCH+assignment,1)
    return text[:start]+changed+text[end:]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def xml_chunks(data):
    if len(data) < 8 or struct.unpack_from('<HHI', data) != (3, 8, len(data)):
        raise ValueError('Expected complete binary Android XML')
    chunks = []
    position = 8
    while position < len(data):
        if position + 8 > len(data):
            raise ValueError('Truncated XML chunk')
        kind, header, size = struct.unpack_from('<HHI', data, position)
        if size < header or header < 8 or position + size > len(data):
            raise ValueError('Invalid XML chunk bounds')
        chunks.append((kind, data[position:position+size]))
        position += size
    return chunks


def string_pool(chunk):
    kind, header, size, count, styles, flags, start, style_start = struct.unpack_from('<HH6I', chunk)
    if kind != 1 or header != 28 or styles or style_start or start != 28 + count*4:
        raise ValueError('Unsupported manifest string pool')
    if size != len(chunk) or start > size:
        raise ValueError('Invalid string pool size')
    offsets = list(struct.unpack_from('<'+'I'*count, chunk, 28))
    strings = []
    def length8(p):
        value = chunk[p]
        return (((value & 127) << 8) | chunk[p+1], p+2) if value & 128 else (value, p+1)
    def length16(p):
        value = struct.unpack_from('<H', chunk, p)[0]
        return ((((value & 32767) << 16) | struct.unpack_from('<H', chunk, p+2)[0]), p+4) if value & 32768 else (value, p+2)
    for offset in offsets:
        p = start + offset
        if p >= size:
            raise ValueError('Invalid string offset')
        if flags & 256:
            _, p = length8(p)
            length, p = length8(p)
            raw = chunk[p:p+length]
            if p+length >= size or chunk[p+length] != 0:
                raise ValueError('Unterminated UTF8 string')
            strings.append(raw.decode('utf-8'))
        else:
            length, p = length16(p)
            if p+length*2+2 > size or chunk[p+length*2:p+length*2+2] != b'\0\0':
                raise ValueError('Unterminated UTF16 string')
            strings.append(chunk[p:p+length*2].decode('utf-16-le'))
    return strings, flags, start


def append_string(chunk, value):
    strings, flags, start = string_pool(chunk)
    if value in strings:
        return chunk, strings.index(value)
    # This editor appends one short, fixed ASCII Android permission name only.
    raw = value.encode('ascii')
    if len(raw) >= 128:
        raise ValueError('Unexpected long permission string')
    encoded = bytes([len(raw), len(raw)])+raw+b'\0' if flags & 256 else struct.pack('<H',len(raw))+value.encode('utf-16-le')+b'\0\0'
    body = chunk[start:]
    count = len(strings)
    offsets = chunk[28:start] + struct.pack('<I', len(body))
    contents = body+encoded
    contents += b'\0' * (-len(contents) % 4)
    size = 28+len(offsets)+len(contents)
    header = struct.pack('<HH6I',1,28,size,count+1,0,flags & ~1,28+len(offsets),0)
    result = header+offsets+contents
    assert string_pool(result)[0] == strings+[value]
    return result, count


def service_permission(data, replacement=None):
    """Read/edit one literal service attribute; leave other components untouched."""
    chunks = xml_chunks(data)
    pools = [i for i,(kind,_) in enumerate(chunks) if kind == 1]
    if len(pools) != 1:
        raise ValueError('Expected one string pool')
    pool_index = pools[0]
    strings, _, _ = string_pool(chunks[pool_index][1])
    matches = []
    for index,(kind,raw) in enumerate(chunks):
        if kind != 0x102:
            continue
        if len(raw)<36:
            raise ValueError('Truncated start element')
        name = struct.unpack_from('<I',raw,20)[0]
        if strings[name] != 'service':
            continue
        offset, step, count = struct.unpack_from('<3H',raw,24)
        if step != 20 or 16+offset+count*step > len(raw):
            raise ValueError('Unexpected attributes layout')
        attributes = {}
        for n in range(count):
            p = 16+offset+n*step
            namespace, name, literal = struct.unpack_from('<3I',raw,p)
            if namespace == 0xffffffff or strings[namespace] != ANDROID:
                continue
            value_size, zero, typ, value = struct.unpack_from('<HBBI',raw,p+12)
            if value_size != 8 or zero:
                raise ValueError('Unexpected typed attribute')
            attributes[strings[name]] = (p,strings[value] if typ==3 else None,typ,literal,value)
        if attributes.get('name',(0,None))[1] == SERVICE:
            permission = attributes.get('permission')
            if not permission or permission[2] != 3:
                raise ValueError('Expected literal service permission')
            matches.append((index,permission))
    if len(matches) != 1:
        raise ValueError('Expected exactly one reviewed predecode service')
    index, (position, value, typ, literal, string_id) = matches[0]
    if replacement is None:
        return value
    if value != OLD_PERMISSION or replacement != NEW_PERMISSION:
        raise ValueError('Unexpected service permission transition')
    pool, new_id = append_string(chunks[pool_index][1],replacement)
    raw = bytearray(chunks[index][1])
    if literal != 0xffffffff and literal != string_id:
        raise ValueError('Raw/typed service permission disagree')
    if literal != 0xffffffff:
        struct.pack_into('<I',raw,position+8,new_id)
    struct.pack_into('<I',raw,position+16,new_id)
    chunks[pool_index] = (1,pool)
    chunks[index] = (0x102,bytes(raw))
    body = b''.join(raw for _,raw in chunks)
    result = struct.pack('<HHI',3,8,8+len(body))+body
    assert service_permission(result) == replacement
    return result


def prepare(apk, android, output, work, flavor):
    if output.exists() or work.exists():
        raise ValueError('Refusing to overwrite APK/work evidence')
    if flavor not in ('portable','lineage') or sha(apk.read_bytes()) != APK_SHA:
        raise ValueError('Unreviewed APK/flavor')
    with zipfile.ZipFile(apk) as z:
        entries = [(info,z.read(info.filename)) for info in z.infolist()]
    original = {info.filename:data for info,data in entries}
    if len(original)!=len(entries) or sha(original['AndroidManifest.xml'])!=MANIFEST_SHA or sha(original['classes5.dex'])!=DEX_SHA:
        raise ValueError('Unreviewed manifest/DEX or duplicate APK members')
    work.mkdir(parents=True)
    java = android/'prebuilts/jdk/jdk21/linux-x86/bin'
    sdk = android/'prebuilts/sdk/36/public/android.jar'
    def run(args):
        p=subprocess.run(list(map(str,args)),capture_output=True,timeout=600)
        if p.returncode:
            raise RuntimeError(p.stderr.decode(errors='replace')[-3000:])
        return p.stdout
    old=original['classes5.dex'];names={name for name,_ in definitions(old)}
    if TYPE not in names:
        raise ValueError('Receiver descriptor absent')
    kept=work/'unchanged.dex';kept.write_bytes(select_definitions(old,names-{TYPE}))
    original_dex=work/'original.dex';original_dex.write_bytes(old)
    smali_tools=android/'prebuilts/extract-tools/common/smali'
    directory=work/'smali'
    run([java/'java','-Xmx512m','-jar',smali_tools/'baksmali.jar','disassemble','--api','31',
        '--use-locals','--classes',TYPE,'--output',directory,original_dex])
    source=directory/'com/oplus/aiunit/vision/ft.smali'
    text=source.read_text();(work/'descriptor-before.smali').write_text(text)
    source.write_text(patch_descriptor_smali(text))
    adapter=work/'adapter.dex'
    run([java/'java','-Xmx512m','-jar',smali_tools/'smali.jar','assemble','--api','31','--output',adapter,directory])
    if {name for name,_ in definitions(adapter.read_bytes())}!={TYPE}:
        raise ValueError('Unexpected adapter classes')
    dexout=work/'dex';dexout.mkdir()
    d8=[java/'java','-Xmx1g','-cp',android/'prebuilts/r8/r8.jar','com.android.tools.r8.D8','--lib',sdk,'--min-api','31']
    run([*d8,'--output',dexout,kept,adapter])
    if sorted(p.name for p in dexout.iterdir())!=['classes.dex']:
        raise ValueError('Unexpected DEX split')
    new=(dexout/'classes.dex').read_bytes()
    if {name for name,_ in definitions(new)}!=names or abi(old,names)!=abi(new,names):
        raise ValueError('Class/public ABI changed')
    post=work/'descriptor-after'
    run([java/'java','-Xmx512m','-jar',smali_tools/'baksmali.jar','disassemble','--api','31',
        '--use-locals','--classes',TYPE,'--output',post,dexout/'classes.dex'])
    def without_constructor(value):
        marker='.method public constructor <init>(Ljava/lang/String;Ljava/lang/String;I)V'
        first=value.index(marker);last=value.index('.end method',first)+len('.end method')
        return value[:first]+value[last:]
    if without_constructor(text)!=without_constructor((post/'com/oplus/aiunit/vision/ft.smali').read_text()):
        raise ValueError('Descriptor metadata/non-constructor methods changed')
    # Canonicalize only the unaffected classes independently to detect collateral
    # changes beyond their public ABI (bodies, private members and annotations).
    for label,data in [('before',select_definitions(old,names-{TYPE})),('after',select_definitions(new,names-{TYPE}))]:
        source=work/(label+'.dex');source.write_bytes(data);directory=work/label;directory.mkdir()
        run([*d8,'--output',directory,source])
    before=(work/'before/classes.dex').read_bytes();after=(work/'after/classes.dex').read_bytes()
    semantic = None
    if before != after:
        # DEX container layout/compiler markers need not be byte canonical.
        # Compare complete class disassembly, not just exported signatures.
        baksmali=android/'prebuilts/extract-tools/common/smali/baksmali.jar'
        checker=ROOT/'tools/oem_gallery_compat/GalleryDexSemanticCheck.java'
        host=work/'semantic-classes';host.mkdir()
        run([java/'javac','--release','17','-cp',baksmali,'-d',host,checker])
        semantic=json.loads(run([java/'java','-Xmx1g','-cp',str(host)+':'+str(baksmali),
            'GalleryDexSemanticCheck',work/'before/classes.dex',work/'after/classes.dex',work/'semantic-diff']))
        if not semantic['matching'] or semantic['classes']!=len(names)-1:
            raise ValueError('Unaffected class bodies/metadata differ')
    manifest = service_permission(original['AndroidManifest.xml'],NEW_PERMISSION) if flavor=='lineage' else original['AndroidManifest.xml']
    output.parent.mkdir(parents=True,exist_ok=True)
    stripped=[]
    with zipfile.ZipFile(output,'x') as z:
        for info,data in entries:
            if info.filename.upper().startswith('META-INF/') and (info.filename.upper().endswith(('.SF','.RSA','.DSA','.EC')) or info.filename.upper()=='META-INF/MANIFEST.MF'):
                stripped.append(info.filename);continue
            z.writestr(info,manifest if info.filename=='AndroidManifest.xml' else new if info.filename=='classes5.dex' else data)
    report={'source_apk_sha256':APK_SHA,'flavor':flavor,'unsigned_apk_sha256':sha(output.read_bytes()),
            'policy_model_source_sha256':sha(SOURCE.read_bytes()),'smali_patch_sha256':sha(FLAGS_PATCH.encode()),
            'patched_descriptor_smali_sha256':sha(source.read_bytes()),'manifest_sha256':sha(manifest),'dex_sha256':sha(new),
            'canonical_container_bytes_equal':before==after,'unaffected_container_before_sha256':sha(before),
            'unaffected_container_after_sha256':sha(after),'full_class_disassembly':semantic,
            'descriptor_class_count':len(names),'public_abi_unchanged':True,
            'descriptor_metadata_and_other_methods_unchanged':True,
            'unaffected_class_semantics_match':True,'stripped_original_signatures':stripped,
            'changed_payload_members':['classes5.dex']+(['AndroidManifest.xml'] if flavor=='lineage' else []),
            'service_permission':service_permission(manifest),'min_sdk':31,'target_sdk':34,
            'limits':'Unsigned offline candidate, original version/target/min SDK retained. Receiver state correction only API34+; explicit flags and older API behavior retained. Needs dedicated app signing, recovery ownership/peer review and fresh device acceptance. Portable flavor retains OEM gate; Lineage flavor uses existing SYSTEM_CAMERA permission on one service. Not proof of arbitrary-ROM compatibility.'}
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('apk','android','output','work'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--flavor',choices=['portable','lineage'],required=True)
    a=p.parse_args();print(json.dumps(prepare(a.apk,a.android,a.output,a.work,a.flavor),indent=2))


if __name__=='__main__':main()
