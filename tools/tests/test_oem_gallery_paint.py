from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oem_gallery_paint import HELPER,METHOD,ROOT,SOURCE,patch_latency,prepare


class PaintPatchTests(unittest.TestCase):
    def specimen(self):
        return ('constructor and legacy hints\n'+METHOD+'\n    .registers 7\n'
                '    return-void\n    :cond_c\n    const-string v0, "uah handle is released"\n'
                '    invoke-static {v0}, Lcom/oplus/uah/UAHResClient;->get(Ljava/lang/Class;)Lcom/oplus/uah/UAHResClient;\n'
                '    invoke-virtual {v0,v1}, Lcom/oplus/uah/UAHResClient;->acquireEvent(Lcom/oplus/uah/info/UAHEventRequest;)I\n'
                '    invoke-virtual {v0,v1}, Lcom/oplus/uah/UAHResClient;->release(I)V\n'
                '    invoke-virtual {v0,v1}, Lcom/oplus/uah/UAHResClient;->release(I)V\n'
                '    return-void\n.end method\nother original methods\n')

    def test_only_optional_hint_body_gets_the_preaccess_guard(self):
        old=self.specimen();new=patch_latency(old)
        added=('    invoke-static {}, '+HELPER+'->available()Z\n\n'
               '    move-result v0\n\n    if-nez v0, :realme_uah_supported\n\n'
               '    return-void\n\n    :realme_uah_supported\n')
        self.assertEqual(new.replace(added,''),old)
        self.assertLess(new.index(HELPER),new.index('UAHResClient;->get'))

    def test_repeated_ambiguous_or_different_abi_is_rejected(self):
        old=self.specimen()
        for text in [patch_latency(old),old+old,old.replace('registers 7','registers 6'),
                     old.replace('release(I)V','release(J)V'),old.replace(':cond_c',':cond_d')]:
            with self.subTest(text=text[:35]),self.assertRaises(ValueError):patch_latency(text)

    def test_foreign_archive_is_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as temporary:
            root=Path(temporary);(root/'foreign.apk').write_bytes(b'foreign')
            with self.assertRaisesRegex(ValueError,'unreviewed Gallery predecessor'):
                prepare(root/'foreign.apk',root/'android',root/'out.apk',root/'work')
            self.assertFalse((root/'out.apk').exists());self.assertFalse((root/'work').exists())


class PerformanceApiTests(unittest.TestCase):
    def test_complete_api_detection_does_not_initialize_or_invoke_the_vendor_service(self):
        jdk=Path('/build/lineage/prebuilts/jdk/jdk21/linux-x86/bin')
        javac=str(jdk/'javac') if (jdk/'javac').is_file() else shutil.which('javac')
        java=str(jdk/'java') if (jdk/'java').is_file() else shutil.which('java')
        if not javac or not java:self.skipTest('JDK unavailable')
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as temporary:
            root=Path(temporary);classes=root/'classes';classes.mkdir()
            event='package com.oplus.uah.info; public class UAHEventRequest { static { if(true) throw new AssertionError("no init"); } public UAHEventRequest(int id,String name,int timeout,java.util.ArrayList list) { throw new AssertionError("no constructor call"); } }'
            client='package com.oplus.uah; import com.oplus.uah.info.UAHEventRequest; public class UAHResClient { static { if(true) throw new AssertionError("no init"); } public static UAHResClient get(Class type) { throw new AssertionError("no get call"); } public void release(int handle) { throw new AssertionError("no release call"); } public int acquireEvent(UAHEventRequest event) { throw new AssertionError("no acquire call"); } }'
            variants=[('complete',client,event),('wrongreturn',client.replace('public void release','public int release'),event),
                      ('wrongconstructor',client,event.replace('java.util.ArrayList list','java.util.List list')),
                      ('staticmethod',client.replace('public void release','public static void release'),event),
                      ('abstractevent',client,event.replace('public class UAHEventRequest','public abstract class UAHEventRequest'))]
            dirs=[]
            for name,c,e in variants:
                stage=root/name;stage.mkdir();out=stage/'out';out.mkdir()
                (stage/'UAHResClient.java').write_text(c);(stage/'UAHEventRequest.java').write_text(e)
                subprocess.run([javac,'--release','8','-d',str(out),str(stage/'UAHResClient.java'),str(stage/'UAHEventRequest.java')],capture_output=True,check=True,timeout=30);dirs.append(out)
            partial=root/'partial/com/oplus/uah';partial.mkdir(parents=True)
            shutil.copy2(dirs[0]/'com/oplus/uah/UAHResClient.class',partial/'UAHResClient.class');dirs.append(root/'partial')
            harness=root/'PerformanceProbe.java';harness.write_text('''import java.net.*; import java.io.*; import realme.gallery.compat.OptionalOemPerformance;
public final class PerformanceProbe {
 static void ok(boolean v) { if(!v) throw new AssertionError(); }
 public static void main(String[] args) throws Exception {
  ok(!OptionalOemPerformance.available());
  for(int i=0;i<args.length;i++) try(URLClassLoader loader=new URLClassLoader(new URL[]{new File(args[i]).toURI().toURL()},null)) {
   ok(OptionalOemPerformance.available(loader)==(i==0));
  }
  ClassLoader broken=new ClassLoader(null) { protected Class<?> findClass(String n) { throw new UnsupportedClassVersionError("synthetic unavailable API"); } };
  ok(!OptionalOemPerformance.available(broken));
  System.out.println("Complete, absent, partial and incompatible APIs handled without vendor initialization or invocation.");
 }
}''')
            subprocess.run([javac,'--release','8','-d',str(classes),str(SOURCE),str(harness)],capture_output=True,check=True,timeout=30)
            p=subprocess.run([java,'-cp',str(classes),'PerformanceProbe',*map(str,dirs)],capture_output=True,text=True,check=True,timeout=30)
            self.assertIn('without vendor initialization or invocation',p.stdout)


if __name__=='__main__':unittest.main()
