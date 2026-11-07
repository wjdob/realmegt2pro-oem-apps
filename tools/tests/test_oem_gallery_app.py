"""Exercise additive API policy and the exact per-component manifest boundary."""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import oem_gallery_app as app
from oem_camera_window import abi

JDK=Path('/build/lineage/prebuilts/jdk/jdk21/linux-x86/bin')


def fixture(utf8=True, duplicate=False, include_new=False):
    strings=[app.ANDROID,'service','name','permission',app.SERVICE,app.OLD_PERMISSION,'other.Service']
    if include_new:strings.append(app.NEW_PERMISSION)
    data=bytearray();offsets=[]
    for s in strings:
        offsets.append(len(data));raw=s.encode('utf-8' if utf8 else 'utf-16-le')
        data.extend(bytes([len(s),len(raw)])+raw+b'\0' if utf8 else struct.pack('<H',len(s))+raw+b'\0\0')
    data.extend(b'\0'*(-len(data)%4));start=28+4*len(strings)
    pool=struct.pack('<HH6I',1,28,start+len(data),len(strings),0,256 if utf8 else 0,start,0)+struct.pack('<'+'I'*len(offsets),*offsets)+data
    def service(name):
        attributes=b''.join(struct.pack('<IIIHBBI',0,key,value,8,0,3,value) for key,value in [(2,name),(3,5)])
        node=struct.pack('<HHIII',0x102,16,36+len(attributes),1,0xffffffff)
        node+=struct.pack('<II6H',0xffffffff,1,20,20,2,0,0,0)+attributes
        end=struct.pack('<HH5I',0x103,16,24,1,0xffffffff,0xffffffff,1)
        return node+end
    body=bytes(pool)+service(4)+service(4 if duplicate else 6)
    return struct.pack('<HHI',3,8,8+len(body))+body


class ManifestBoundaryTests(unittest.TestCase):
    def test_smali_patch_is_constructor_only_and_rejects_unknown_input(self):
        text='''.class public final Lcom/oplus/aiunit/vision/ft;
.method public constructor <init>(Ljava/lang/String;Ljava/lang/String;I)V
    .locals 1
    iput p3, p0, Lcom/oplus/aiunit/vision/ft;->c:I
    return-void
.end method
.method public hashCode()I
    .locals 1
    const/4 v0, 0x0
    return v0
.end method
'''
        after=app.patch_descriptor_smali(text)
        self.assertEqual(text.split('.end method',1)[1],after.split('.end method',1)[1])
        self.assertIn('    .locals 2',after)
        self.assertIn('if-lt v0, v1, :lineage_gallery_flags_original',after)
        for value in (text.replace('.locals 1','.locals 9'),after,text+text):
            with self.subTest(size=len(value)),self.assertRaises(ValueError):app.patch_descriptor_smali(value)

    def test_dex_abi_reader_accepts_no_class_data(self):
        data=bytearray(256);data[:8]=b'dex\n039\0'
        strings=[b'LEmpty;',b'Ljava/lang/Object;'];position=144
        struct.pack_into('<II',data,56,2,112);struct.pack_into('<II',data,64,2,120)
        struct.pack_into('<II',data,96,1,208)
        for i,value in enumerate(strings):
            struct.pack_into('<I',data,112+4*i,position)
            data[position:position+len(value)+2]=bytes([len(value)])+value+b'\0';position+=len(value)+2
            struct.pack_into('<I',data,120+4*i,i)
        struct.pack_into('<8I',data,208,0,1,1,0,0xffffffff,0,0,0)
        self.assertEqual(abi(bytes(data),{'LEmpty;'}),{'LEmpty;':dict(superclass='Ljava/lang/Object;',access=1,fields={},methods={})})

    def test_utf8_and_utf16_change_only_reviewed_component_and_pool(self):
        for utf8 in (True,False):
            with self.subTest(utf8=utf8):
                before=fixture(utf8);after=app.service_permission(before,app.NEW_PERMISSION)
                self.assertEqual(app.service_permission(after),app.NEW_PERMISSION)
                old=app.xml_chunks(before);new=app.xml_chunks(after)
                self.assertEqual(len(old),len(new))
                self.assertEqual(old[2:],new[2:]) # end tag and other service exact
                self.assertEqual(app.string_pool(new[0][1])[0],app.string_pool(old[0][1])[0]+[app.NEW_PERMISSION])
                b=bytearray(old[1][1]);a=bytearray(new[1][1])
                # Exactly the permission's raw string and typed string indexes.
                for offset in (36+20+8,36+20+16):a[offset:offset+4]=b[offset:offset+4]
                self.assertEqual(a,b)

    def test_existing_string_reused_without_pool_changes(self):
        before=fixture(include_new=True);after=app.service_permission(before,app.NEW_PERMISSION)
        self.assertEqual(app.xml_chunks(before)[0],app.xml_chunks(after)[0])

    def test_unknown_duplicate_or_already_modified_target_refused(self):
        with self.assertRaises(ValueError):app.service_permission(fixture(duplicate=True),app.NEW_PERMISSION)
        changed=app.service_permission(fixture(),app.NEW_PERMISSION)
        with self.assertRaises(ValueError):app.service_permission(changed,app.NEW_PERMISSION)
        with self.assertRaises(ValueError):app.service_permission(fixture(),'android.permission.INTERNET')

    def test_truncated_or_invalid_chunks_refused(self):
        for value in (b'',fixture()[:-1],b'not binary xml'):
            with self.subTest(size=len(value)),self.assertRaises(ValueError):app.service_permission(value,app.NEW_PERMISSION)
        broken=bytearray(fixture());struct.pack_into('<I',broken,12,0xffffffff)
        with self.assertRaises(ValueError):app.service_permission(bytes(broken),app.NEW_PERMISSION)


@unittest.skipUnless((JDK/'javac').is_file(),'requires WSL realme-build JDK21')
class ReceiverCompatibilityTests(unittest.TestCase):
    def test_old_api_explicit_export_permissions_flags_and_value_semantics(self):
        root=ROOT/'tmp/oem-gallery-app-tests';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            work=Path(temporary);stub=work/'android/os/Build.java';stub.parent.mkdir(parents=True)
            stub.write_text('package android.os; public class Build { public static class VERSION { public static int SDK_INT; } }')
            probe=work/'Probe.java';probe.write_text('''
import com.oplus.aiunit.vision.ft;
import android.os.Build;
public class Probe {
 static void check(boolean condition) { if(!condition)throw new AssertionError(); }
 public static void main(String[] ignored) throws Exception {
  int[] flags={0,1,2,4,6,8,9,10,12,0x1000000};int count=0;
  for(int api:new int[]{31,32,33,34,35,36}) {
   Build.VERSION.SDK_INT=api;
   for(int flag:flags) for(String permission:new String[]{null,"","example.signature.PERMISSION"}) {
    int wanted=api>=34&&(flag&7)==0?flag|((permission==null||permission.isEmpty())?4:2):flag;
    ft value=new ft("example.private.EVENT",permission,flag);
    check(value.c==wanted);check(value.a.equals("example.private.EVENT"));check(java.util.Objects.equals(permission,value.b));
    ft same=new ft("example.private.EVENT",permission,flag);check(value.equals(same));check(value.hashCode()==same.hashCode());
    check(!value.equals(null)&&!value.equals("other"));count++;
   }
  }
  Build.VERSION.SDK_INT=31;ft value=new ft("a",null,0);check(value.toString().equals("ActionReceiverProperties(action=a, permission=null, flags=0)"));
  check(value.hashCode()=="a".hashCode()*31*31);check(!value.equals(new ft("b",null,0)));
  try {new ft(null,null,0);throw new AssertionError();}catch(NullPointerException expected){}
  for(String field:new String[]{"a","b","c"})check(java.lang.reflect.Modifier.isFinal(ft.class.getField(field).getModifiers()));
  System.out.println("PASS "+count+" API/flag/permission cases");
 }
}''')
            p=subprocess.run([str(JDK/'javac'),'--release','8','-d',str(work),str(stub),str(app.SOURCE),str(probe)],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            p=subprocess.run([str(JDK/'java'),'-cp',str(work),'Probe'],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr);self.assertIn('PASS 180',p.stdout)


if __name__=='__main__':unittest.main()
