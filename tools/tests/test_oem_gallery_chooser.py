from pathlib import Path
import sys
import unittest
import shutil
import subprocess
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oem_gallery_chooser import HELPER, METHOD, patch_labels, patch_share_mime


class ChooserLabelTests(unittest.TestCase):
    def specimen(self):
        return ('unrelated prefix\n' + METHOD + '\n    .registers 15\n'
                '    invoke-static {v1, p2}, ' + HELPER
                + '->label(Landroid/content/Context;Landroid/content/pm/ResolveInfo;)Ljava/lang/String;\n\n'
                '    move-result-object v1\n\n    const-string v2, ""\n'
                '    move-object v4, p3\n    move-object v5, p2\n    return-object v0\n'
                '    # supported OEM item and original dispatch\n'
                '    move-object v4, p3\n    move-object v5, p2\n'
                '    move-object v4, p3\n    move-object v5, p2\n.end method\nunrelated suffix')

    def test_visible_text_and_optional_label_are_distinct(self):
        old = self.specimen()
        new = patch_labels(old)
        self.assertIn('move-result-object v2\n\n    const/4 v1, 0x0', new)
        self.assertEqual(old.split('    return-object v0')[1], new.split('    return-object v0')[1])
        self.assertEqual(old.count('move-object v5, p2'), new.count('move-object v5, p2'))
        self.assertTrue(new.startswith('unrelated prefix\n') and new.endswith('unrelated suffix'))

    def test_reapplication_or_changed_choice_contract_is_rejected(self):
        for value in (patch_labels(self.specimen()),
                      self.specimen().replace('move-object v5, p2', 'move-object v5, p1', 1),
                      self.specimen().replace('.registers 15', '.registers 16'),
                      self.specimen().replace('const-string v2, ""', 'const-string v2, "OEM"'),
                      self.specimen().replace(METHOD, METHOD+'\n'+METHOD)):
            with self.subTest(value=value[:55]):
                with self.assertRaises(ValueError):
                    patch_labels(value)

    def test_single_mime_change_keeps_stream_and_read_grant_flow(self):
        before = ('prefix\n.method public static final j(Ljava/util/Set;Z)Landroid/content/Intent;\n'
                  '    .registers 16\n    # Original URI, metadata and privacy selection\n'
                  '    invoke-virtual {v2, p1}, Landroid/content/Intent;->setType(Ljava/lang/String;)Landroid/content/Intent;\n'
                  '    # Original logging\n'
                  '    invoke-virtual {v2, v0}, Landroid/content/Intent;->addFlags(I)Landroid/content/Intent;\n'
                  '    return-object v2\n.end method\n'
                  '.method public static final o(Landroid/content/Intent;Ljava/util/Set;Ljava/util/Map;)V\n'
                  '    .registers 13\n    # Original converted streams and read grants\n'
                  '    invoke-virtual {p0, p1, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/io/Serializable;)Landroid/content/Intent;\n'
                  '    new-instance p0, Ljava/lang/StringBuilder;\n'
                  '    return-void\n.end method\nsuffix')
        after = patch_share_mime(before)
        self.assertEqual(before.split('    invoke-virtual {v2, p1}')[0], after.split('    invoke-virtual {v2, p1}')[0])
        self.assertEqual(before.split('    # Original logging')[1].split('.end method')[0],
                         after.split('    # Original logging')[1].split('.end method')[0])
        self.assertEqual(before.split('    new-instance p0, Ljava/lang/StringBuilder;')[1],
                         after.split('    new-instance p0, Ljava/lang/StringBuilder;')[1])
        self.assertIn('SingleShareMime;->refine', after)
        self.assertIn('move-result-object v2', after)
        self.assertIn('SingleShareMime;->refresh', after)
        for invalid in (after, before.replace('.registers 16', '.registers 15'),
                        before.replace('addFlags(I)', 'setFlags(I)'),
                        before.replace('.registers 13', '.registers 12')):
            with self.assertRaises(ValueError): patch_share_mime(invalid)

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK required for actual MIME policy')
    def test_actual_java_policy_preserves_unknown_multiple_and_cross_family_inputs(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix='gallery-mime-policy-') as scratch:
            result = subprocess.run(['javac', '--release', '8', '-d', scratch,
                str(root/'tools/oem_gallery_compat/SingleShareMimePolicy.java'),
                str(root/'tools/tests/java/SingleShareMimePolicyTest.java')], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(['java', '-cp', scratch, 'SingleShareMimePolicyTest'],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('cases=33', result.stdout)


if __name__ == '__main__':
    unittest.main()
