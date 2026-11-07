"""Exercise actual optional-app Java helpers and version-bound DEX adapters."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import oem_gallery_heic as heic

FAKES={
 'android/content/pm/ApplicationInfo.java':'''package android.content.pm;public class ApplicationInfo {public String sourceDir="missing";}''',
 'android/content/Context.java':'''package android.content;
 public class Context {private final String pkg;private final android.content.pm.ApplicationInfo info=new android.content.pm.ApplicationInfo();
 public Context(String p){pkg=p;}public String getPackageName(){return pkg;}
 public android.content.pm.ApplicationInfo getApplicationInfo(){return info;}public ClassLoader getClassLoader(){return Context.class.getClassLoader();}}''',
 'android/app/Activity.java':'''package android.app;public class Activity extends android.content.Context {
 public Activity(String p){super(p);}public boolean isDestroyed(){return false;}}''',
 'android/os/Build.java':'''package android.os;public class Build {public static class VERSION {public static int SDK_INT=31;}}''',
 'android/util/Log.java':'''package android.util;public class Log {public static int i(String t,String m){return 0;}public static int w(String t,String m){return 0;}}''',
 'android/graphics/ColorSpace.java':'''package android.graphics;public class ColorSpace {public enum Named {SRGB}public static ColorSpace get(Named n){return new ColorSpace();}}''',
 'android/graphics/Bitmap.java':'''package android.graphics;import java.io.OutputStream;
 public class Bitmap {public enum Config{ARGB_8888}public enum CompressFormat{JPEG}
 public static boolean fail,result=true;public static int recycled,quality;
 public boolean compress(CompressFormat f,int q,OutputStream o){quality=q;if(fail)throw new IllegalStateException();
 try{o.write(new byte[]{1,2,3});}catch(java.io.IOException e){return false;}return result;}public void recycle(){recycled++;}}''',
 'android/graphics/BitmapFactory.java':'''package android.graphics;public class BitmapFactory {
 public static String mime="image/heif";public static int width=4,height=3;
 public static class Options {public boolean inJustDecodeBounds;public String outMimeType;public int outWidth,outHeight;
 public Bitmap.Config inPreferredConfig;public ColorSpace inPreferredColorSpace;}
 public static Bitmap decodeByteArray(byte[] b,int off,int len,Options o){o.outMimeType=mime;o.outWidth=width;o.outHeight=height;
 return o.inJustDecodeBounds?null:new Bitmap();}}''',
 'android/media/MediaCodecInfo.java':'''package android.media;public class MediaCodecInfo {
 private final String mime;private final VideoCapabilities v;
 public MediaCodecInfo(String m,int w,int h){mime=m;v=new VideoCapabilities(w,h);}public boolean isEncoder(){return true;}
 public String[] getSupportedTypes(){return new String[]{mime};}public CodecCapabilities getCapabilitiesForType(String t){return new CodecCapabilities(v);}
 public static class CodecCapabilities {public static final int COLOR_FormatSurface=0x7f000789;public int[] colorFormats={COLOR_FormatSurface};
 private final VideoCapabilities v;public CodecCapabilities(VideoCapabilities a){v=a;}public VideoCapabilities getVideoCapabilities(){return v;}}
 public static class VideoCapabilities {private final int w,h;public VideoCapabilities(int a,int b){w=a;h=b;}
 public boolean isSizeSupported(int a,int b){return a<=w&&b<=h;}}}''',
 'android/media/MediaCodecList.java':'''package android.media;public class MediaCodecList {public static final int ALL_CODECS=1;
 public static MediaCodecInfo[] infos=new MediaCodecInfo[0];public MediaCodecList(int m){}public MediaCodecInfo[] getCodecInfos(){return infos;}}''',
 'com/oplus/camera/common/config/CameraSettingsConfig.java':'''package com.oplus.camera.common.config;public class CameraSettingsConfig {
 public static final java.util.List<OptionItemConfig> mOptionItemConfigList=new java.util.ArrayList<>();}''',
 'com/oplus/camera/common/config/OptionItemConfig.java':'''package com.oplus.camera.common.config;public class OptionItemConfig {
 private String key;private final java.util.List<String> sizes=new java.util.ArrayList<>();public OptionItemConfig(){}
 public void setKey(String s){key=s;}public String getKey(){return key;}public java.util.List<String> getSupportedList(){return sizes;}
 public void addOptionItem(String s){sizes.add(s);}}''',
 'com/oplus/camera/util/Util.java':'''package com.oplus.camera.util;public class Util {public static Boolean r=false;}''',
 'c9/b.java':'''package c9;public class b {public static Boolean a=false;}''',
}


class FormatsAdaptersTests(unittest.TestCase):
    def capability(self):
        return '''.method public static final b(Landroid/content/Context;)V
    .registers 10
    invoke-static {}, Lcom/oplus/aiunit/vision/m55;->q()Z
    move-result v0
    if-nez v0, :cond_3c
    :cond_3c
    sput-boolean v7, Lcom/oplus/aiunit/vision/wr6;->b:Z
    return-void
.end method
.method private unrelated()V
    .registers 1
    return-void
.end method'''

    def converter(self):
        return '''.method public static final b(Ljava/io/InputStream;ILjava/io/OutputStream;)Z
    .registers 4
    invoke-static {p0, p1, p2}, Lcom/heytap/addon/media/OplusHeifConverter;->convertHeifToJpegFromStream(Ljava/io/InputStream;ILjava/io/OutputStream;)Z
    move-result p0
    return p0
.end method'''

    def test_capability_preserves_native_branch_and_other_methods(self):
        before=self.capability();after=heic.patch_capability(before)
        anchor='    invoke-static {}, Lcom/oplus/aiunit/vision/m55;->q()Z'
        self.assertEqual(after[after.index(anchor):],before[before.index(anchor):])
        self.assertIn('->enabled(Ljava/lang/String;)Z',after)
        self.assertNotIn('putBoolean',after)

    def test_converter_keeps_original_oem_call(self):
        after=heic.patch_converter(self.converter())
        self.assertEqual(after.count('OplusHeifConverter;->convertHeifToJpegFromStream'),1)
        self.assertIn(':realme_heic_original_converter',after)
        self.assertIn(heic.HELPER+'->convert(',after)

    def test_duplicate_unknown_or_repeated_targets_refuse(self):
        for before,patch in ((self.capability(),heic.patch_capability),(self.converter(),heic.patch_converter)):
            with self.assertRaises(ValueError):patch(before+before)
            with self.assertRaises(ValueError):patch(patch(before))
            with self.assertRaises(ValueError):patch(before.replace('.registers 10','.registers 11').replace('.registers 4','.registers 5'))

    def test_unknown_apk_does_not_create_or_overwrite_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);apk=root/'input';apk.write_bytes(b'unknown')
            output=root/'output';output.write_bytes(b'preserve')
            with self.assertRaises(ValueError):heic.prepare(apk,root,output,root/'work')
            self.assertEqual(output.read_bytes(),b'preserve');self.assertFalse((root/'work').exists())

    @unittest.skipUnless(Path('/build/lineage24-store/tree/prebuilts/jdk/jdk21/linux-x86/bin/javac').is_file(),'AOSP JDK required')
    def test_current_bridge_keeps_popup_and_lens_protocol(self):
        import test_oem_camera_lens16 as protocol
        import oem_camera_gallery as gallery
        original=protocol.ast.literal_eval
        def fixtures(node):
            values=original(node)
            # The codec helper's transaction is tested separately with its real
            # source. Here its failure must leave viewer/Lens behavior intact.
            values['realme/camera/compat/EncoderMenuCompatibility.java']='''package realme.camera.compat;
public class EncoderMenuCompatibility {public static boolean apply(android.app.Activity a){return false;}}'''
            return values
        selected=gallery.sources({'adapter_source_directory':gallery.FORMAT_DIRECTORY})
        with patch.object(protocol.ast,'literal_eval',side_effect=fixtures),patch.object(protocol.gallery,'sources',return_value=selected[:-1]):
            protocol.Lens16ProtocolTests('test_popup_capture_review_fallback_grants_lock_and_native_ownership').test_popup_capture_review_fallback_grants_lock_and_native_ownership()


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'),'JDK required')
class FormatsJavaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='oem-formats-');cls.root=Path(cls.temp.name)
        cls.classes=cls.root/'classes';cls.classes.mkdir();inputs=[]
        for name,text in FAKES.items():
            p=cls.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(textwrap.dedent(text));inputs.append(p)
        inputs += [ROOT/'tools/oem_gallery_compat/HeicCompatibility.java',
                   ROOT/'tools/oem_camera_compat/EncoderMenuCompatibility.java',
                   ROOT/'tools/tests/java/HeicCompatibilityTest.java',
                   ROOT/'tools/tests/java/EncoderMenuCompatibilityTest.java']
        p=subprocess.run(['javac','--release','17','-d',str(cls.classes),*map(str,inputs)],capture_output=True,text=True,timeout=60)
        if p.returncode:raise AssertionError(p.stderr)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def execute(self,main,*extra):
        p=subprocess.run(['java','-Xmx512m',*extra,'-cp',str(self.classes),main],capture_output=True,text=True,timeout=60)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr);return p.stdout

    def test_heic_fallback_stream_and_failure_cases(self):
        self.assertIn('HEIC cases passed: 11',self.execute('realme.gallery.compat.HeicCompatibilityTest'))

    def test_heic_original_boot_api_is_preserved(self):
        root=self.root/'boot';source=root/'com/oplus/media/OplusHeifConverter.java';source.parent.mkdir(parents=True,exist_ok=True)
        source.write_text('package com.oplus.media;public class OplusHeifConverter {public static boolean convertHeifToJpegFromStream(java.io.InputStream in,int quality,java.io.OutputStream out){return true;}}')
        p=subprocess.run(['javac','--release','17','-d',str(root),str(source)],capture_output=True,text=True,timeout=30)
        self.assertEqual(p.returncode,0,p.stderr)
        p=subprocess.run(['java','--patch-module','java.base='+str(root),'-cp',str(self.classes),'realme.gallery.compat.HeicCompatibilityTest','oem'],capture_output=True,text=True,timeout=30)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.assertIn('OEM boot converter retained',p.stdout)

    def test_encoder_menu_transaction_and_refusals(self):
        self.assertIn('Encoder menu cases passed: 12',self.execute('realme.camera.compat.EncoderMenuCompatibilityTest'))


if __name__=='__main__':unittest.main()
