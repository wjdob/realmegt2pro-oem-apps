from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bootstrap_gallery_dex import verified_paths

ROOT=Path(__file__).resolve().parents[2]
A=Path('/build/lineage');JAVA=A/'prebuilts/jdk/jdk21/linux-x86/bin';SMALI=A/'prebuilts/extract-tools/common/smali'


@unittest.skipUnless((JAVA/'javac').exists(),'requires preserved WSL JDK/smali')
class OriginalDexComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=ROOT/'tmp/gallery-dex-semantic-tests';root.mkdir(parents=True,exist_ok=True)
        cls.tmp=tempfile.TemporaryDirectory(dir=root);cls.work=Path(cls.tmp.name)
        cp=':'.join(map(str,[*verified_paths(),SMALI/'baksmali.jar']))
        subprocess.run([str(JAVA/'javac'),'--release','17','-cp',cp,'-d',str(cls.work),str(ROOT/'tools/oem_gallery_compat/GalleryDexSemanticCheck.java')],check=True,capture_output=True)
        cls.specimen='''.class public Ltest/Unchanged;
.super Ljava/lang/Object;
.source "Retain.kt"
.annotation build Lkotlin/jvm/internal/SourceDebugExtension;
    value = {"SMAP-original"}
.end annotation
.field public static value:I = 0x1
.method public static answer()I
    .registers 1
    const/4 v0, 0x1
    return v0
.end method
'''

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def compare(self,modified,label):
        for name,text in [('before',self.specimen),('after',modified)]:
            folder=self.work/(label+'-'+name);folder.mkdir();(folder/'Unchanged.smali').write_text(text)
            subprocess.run([str(JAVA/'java'),'-jar',str(SMALI/'smali.jar'),'assemble','--api','31','--output',str(folder.with_suffix('.dex')),str(folder)],check=True,capture_output=True)
        cp=str(self.work)+':'+str(SMALI/'baksmali.jar')
        p=subprocess.run([str(JAVA/'java'),'-cp',cp,'GalleryDexSemanticCheck',str(self.work/(label+'-before.dex')),str(self.work/(label+'-after.dex')),str(self.work/(label+'-diff')),'--normalize-defaults'],capture_output=True,text=True)
        return p.returncode,json.loads(p.stdout)

    def test_original_body_and_annotations_match(self):
        code,r=self.compare(self.specimen,'same');self.assertEqual(code,0);self.assertTrue(r['matching'])

    def test_debug_annotation_loss_is_not_normalized_away(self):
        changed=self.specimen.replace('.annotation build Lkotlin/jvm/internal/SourceDebugExtension;\n    value = {"SMAP-original"}\n.end annotation\n','')
        code,r=self.compare(changed,'annotation');self.assertEqual(code,1);self.assertEqual(r['mismatch_count'],1)

    def test_nondefault_static_value_change_is_rejected(self):
        code,r=self.compare(self.specimen.replace('value:I = 0x1','value:I = 0x0'),'field')
        self.assertEqual(code,1);self.assertFalse(r['matching'])

    def test_method_body_change_is_rejected(self):
        code,r=self.compare(self.specimen.replace('const/4 v0, 0x1','const/4 v0, 0x2'),'body')
        self.assertEqual(code,1);self.assertFalse(r['matching'])

if __name__=='__main__':unittest.main()
