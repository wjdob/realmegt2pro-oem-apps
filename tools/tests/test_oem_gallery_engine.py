from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oem_gallery_engine import CONSTRUCTOR,HAPTICS,LOADER,MAGIC,NATIVE,ROOT,TOOLKIT,patch_loader,patch_toolkit,prepare


class PatchTests(unittest.TestCase):
    def specimen(self):
        return ('metadata\n'+CONSTRUCTOR+'\n    .registers 30\n'
                '    invoke-static/range {p1 .. p1}, Lrealme/gallery/compat/DoodleTheme;->context(Landroid/content/Context;)Landroid/content/Context;\n'
                '    .line 43\n    :try_start_15e\n    nop\n'
                '    :goto_177\n    sget-object v0, '+TOOLKIT+'->mLinearmotorVibrator:Lcom/oplus/os/LinearmotorVibrator;\n'
                '    const-string v2, "vibrator"\n    return-void\n.end method\nother methods\n')

    def test_guard_only_skips_optional_oem_init_and_keeps_android_fallback(self):
        old=self.specimen();new=patch_toolkit(old)
        added=('    invoke-static {}, '+HAPTICS+'->available()Z\n\n'
               '    move-result v0\n\n    if-eqz v0, :goto_177\n\n')
        self.assertEqual(new.replace(added,''),old)
        self.assertLess(new.index(added),new.index(':try_start_15e'))
        for text in [new,old+old,old.replace('registers 30','registers 29'),
                     old.replace('"vibrator"','"other"'),old.replace(':goto_177',':cond_177')]:
            with self.subTest(text=text[:40]),self.assertRaises(ValueError):patch_toolkit(text)

    def test_native_patch_preserves_failure_handler_and_every_other_instruction(self):
        call='    invoke-static {p1}, Ljava/lang/System;->load(Ljava/lang/String;)V'
        old='metadata\n'+LOADER+'\n    .registers 4\n'+call+'\n    .catch Ljava/lang/UnsatisfiedLinkError; {:try_start_0 .. :try_end_1} :catch_2\n.end method\nother method\n'
        new=patch_loader(old)
        self.assertEqual(new.replace(NATIVE,'Ljava/lang/System;'),old)
        for text in [new,old+old,old.replace('registers 4','registers 3'),old.replace('UnsatisfiedLinkError','Throwable')]:
            with self.subTest(text=text[:40]),self.assertRaises(ValueError):patch_loader(text)

    def test_foreign_input_is_rejected_without_partial_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as temporary:
            root=Path(temporary);(root/'in.apk').write_bytes(b'foreign')
            with self.assertRaisesRegex(ValueError,'unreviewed Gallery predecessor'):
                prepare(root/'in.apk',root/'android',root/'out.apk',root/'work')
            self.assertFalse((root/'out.apk').exists());self.assertFalse((root/'work').exists())


class JavaHelperTests(unittest.TestCase):
    def test_real_selection_and_optional_class_detection_without_native_execution(self):
        jdk=Path('/build/lineage/prebuilts/jdk/jdk21/linux-x86/bin')
        javac=str(jdk/'javac') if (jdk/'javac').is_file() else shutil.which('javac')
        java=str(jdk/'java') if (jdk/'java').is_file() else shutil.which('java')
        if not javac or not java:self.skipTest('JDK unavailable')
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as temporary:
            root=Path(temporary);classes=root/'classes';classes.mkdir();oem=root/'oem';oem.mkdir()
            (root/'Build.java').write_text('package android.os; public final class Build { public static String MODEL; public static class VERSION { public static int SDK_INT; } }')
            (root/'LinearmotorVibrator.java').write_text('package com.oplus.os; public final class LinearmotorVibrator { static { if (true) throw new AssertionError("must not initialize OEM classes"); } }')
            (root/'WaveformEffect.java').write_text('package com.oplus.os; public final class WaveformEffect { public static final class Builder { } }')
            (root/'EngineProbe.java').write_text('''import android.os.Build;
import java.net.*;
import java.io.*;
import realme.gallery.compat.*;
public final class EngineProbe {
 static void ok(boolean v) { if(!v) throw new AssertionError(); }
 public static void main(String[] args) throws Exception {
  String tail="/com.coloros.gallery3d/files/component/EliminatePenSourceV2/libcv_inpainting.so";
  Build.MODEL="RMX3301"; Build.VERSION.SDK_INT=36;
  for(String prefix:new String[]{"/data/data","/data/user/0","/data/user/10"})
   ok("realme_cv_inpainting_v10".equals(RemoverNativeCompat.packagedLibrary(prefix+tail)));
  for(String value:new String[]{null,"",tail,"/data/user/../0"+tail,"/data/user/-1"+tail,"/data/user/0//"+tail,
    "/data/user/0"+tail+".other","/data/user/0"+tail.replace("SourceV2","SourceV3"),
    "/data/user/0"+tail.replace("com.coloros.gallery3d","other.package")})
   ok(RemoverNativeCompat.packagedLibrary(value)==null);
  Build.VERSION.SDK_INT=35; ok(RemoverNativeCompat.packagedLibrary("/data/data"+tail)==null);
  Build.VERSION.SDK_INT=36;
  for(String model:new String[]{null,"","other"}) { Build.MODEL=model; ok(RemoverNativeCompat.packagedLibrary("/data/data"+tail)==null); }
  ok(!OptionalOemHaptics.available());
  try(URLClassLoader full=new URLClassLoader(new URL[]{new File(args[0]).toURI().toURL()},null)) {
   ok(OptionalOemHaptics.available(full));
  }
  try(URLClassLoader partial=new URLClassLoader(new URL[]{new File(args[1]).toURI().toURL()},null)) {
   ok(!OptionalOemHaptics.available(partial));
  }
  ClassLoader broken=new ClassLoader(null) { protected Class<?> findClass(String n) { throw new UnsupportedClassVersionError("synthetic incomplete OEM API"); } };
  ok(!OptionalOemHaptics.available(broken));
  System.out.println("Selection and optional OEM detection pass; no native library executed.");
 }
}''')
            subprocess.run([javac,'--release','8','-d',str(oem),str(root/'LinearmotorVibrator.java'),str(root/'WaveformEffect.java')],capture_output=True,check=True,timeout=45)
            sources=[root/'Build.java',root/'EngineProbe.java',ROOT/'tools/oem_gallery_compat/RemoverNativeCompat.java',ROOT/'tools/oem_gallery_compat/OptionalOemHaptics.java']
            subprocess.run([javac,'--release','8','-d',str(classes),*map(str,sources)],capture_output=True,check=True,timeout=45)
            partial=root/'partial/com/oplus/os';partial.mkdir(parents=True)
            for name in ['LinearmotorVibrator.class','WaveformEffect.class']:
                shutil.copy2(oem/'com/oplus/os'/name,partial/name)
            result=subprocess.run([java,'-cp',str(classes),'EngineProbe',str(oem),str(root/'partial')],capture_output=True,text=True,check=True,timeout=30)
            self.assertIn('no native library executed',result.stdout)


if __name__=='__main__':unittest.main()
