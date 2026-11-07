from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oem_gallery_remover import HELPER, METHOD, ROOT, SOURCE, helper_entry, patch_request, prepare


class RequestPatchTests(unittest.TestCase):
    def specimen(self):
        return ('metadata\n'+METHOD+'\n    .registers 12\n'
                '    invoke-static {p3}, Lcom/oplus/aiunit/vision/jv6;->d(Z)Ljava/util/Map;\n\n'
                '    move-result-object p2\n\n'
                '    const-string p3, "aimodeltype"\n'
                '    return-void\n.end method\nother method\n')

    def test_existing_construction_preserved_with_one_postconstruction_call(self):
        before = self.specimen()
        after = patch_request(before)
        added = '\n    invoke-static {p2}, '+HELPER+'->complete(Ljava/util/Map;)V\n'
        self.assertEqual(after.replace(added, ''), before)
        self.assertEqual(after.count(HELPER), 1)
        self.assertLess(after.index('move-result-object p2'), after.index(HELPER))
        self.assertLess(after.index(HELPER), after.index('"aimodeltype"'))

    def test_repeat_ambiguous_or_foreign_preimage_refused(self):
        old = self.specimen()
        for value in [patch_request(old), old+old, '', old.replace('registers 12', 'registers 13'),
                      old.replace('move-result-object p2', 'move-result-object p1')]:
            with self.subTest(value=value[:45]):
                with self.assertRaises(ValueError):
                    patch_request(value)

    def test_foreign_apk_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as temporary:
            path = Path(temporary)
            (path/'foreign.apk').write_bytes(b'foreign APK')
            with self.assertRaisesRegex(ValueError, 'unreviewed Gallery predecessor'):
                prepare(path/'foreign.apk', path/'android', path/'out.apk', path/'work')
            self.assertFalse((path/'work').exists())
            self.assertFalse((path/'out.apk').exists())

    def test_added_member_keeps_predecessor_metadata(self):
        old = zipfile.ZipInfo('classes6.dex', (2025, 2, 4, 6, 8, 10))
        old.compress_type = zipfile.ZIP_DEFLATED
        old.create_system = 3
        old.external_attr = 0o100644 << 16
        new = helper_entry(old)
        self.assertEqual(new.filename, 'classes12.dex')
        for attribute in ['date_time', 'compress_type', 'create_system', 'external_attr', 'internal_attr']:
            self.assertEqual(getattr(new, attribute), getattr(old, attribute))


class RequestCoreJavaTests(unittest.TestCase):
    def test_actual_helper_preserves_oem_values_and_unrelated_request_fields(self):
        jdk = Path('/build/lineage/prebuilts/jdk/jdk21/linux-x86/bin')
        javac = str(jdk/'javac') if (jdk/'javac').is_file() else shutil.which('javac')
        java = str(jdk/'java') if (jdk/'java').is_file() else shutil.which('java')
        if not javac or not java:
            self.skipTest('host JDK unavailable')
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as temporary:
            path = Path(temporary)
            stub = path/'Build.java'
            stub.write_text('package android.os; public final class Build { public static String MODEL; }')
            harness = path/'RequestProbe.java'
            harness.write_text('''import android.os.Build;
import java.util.*;
import realme.gallery.compat.ModelRequestCompat;
public final class RequestProbe {
  static void ok(boolean result) { if (!result) throw new AssertionError(); }
  public static void main(String[] args) {
    Build.MODEL="RMX3301";
    Map<String,String> request=new LinkedHashMap<>();
    request.put("androidVersion","16"); request.put("duId", "");
    request.put("openId","synthetic-test-id"); request.put("sign","existing-test-signature");
    Map<String,String> original=new LinkedHashMap<>(request);
    ModelRequestCompat.complete(request);
    ok("V15.0.0".equals(request.get("oplusOSVersion")));
    Map<String,String> repeated=new LinkedHashMap<>(request);
    ModelRequestCompat.complete(request); ok(request.equals(repeated));
    request.remove("oplusOSVersion"); ok(request.equals(original));
    for (String value : new String[]{"V14.0.0", "V16.0.0", "", null}) {
      request.put("oplusOSVersion", value);
      Map<String,String> expected=new LinkedHashMap<>(request);
      ModelRequestCompat.complete(Collections.unmodifiableMap(request));
      ok(expected.equals(request));
    }
    for (String model : new String[]{"different-model", "", null}) {
      Build.MODEL=model; request.remove("oplusOSVersion");
      Map<String,String> expected=new LinkedHashMap<>(request);
      ModelRequestCompat.complete(Collections.unmodifiableMap(request));
      ok(expected.equals(request));
    }
    ModelRequestCompat.complete(null);
    System.out.println("Request helper cases passed; synthetic Android Build only.");
  }
}''')
            subprocess.run([javac, '--release', '8', '-d', str(path), str(stub), str(SOURCE),
                            str(harness)], capture_output=True, check=True, timeout=60)
            result = subprocess.run([java, '-cp', str(path), 'RequestProbe'], capture_output=True,
                                    text=True, check=True, timeout=30)
            self.assertIn('Request helper cases passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
