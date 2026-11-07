"""Exercise strict generation guards without widening the accepted API36 engine."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_platform as platform
import oem_retention as retention

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / 'tools/oem_addons/update-binary.sh'


class PlatformTests(unittest.TestCase):
    def test_only_reviewed_generation_pairs_are_accepted(self):
        self.assertEqual(platform.checked({}), platform.LEGACY)
        for pair in (platform.LEGACY, platform.ANDROID17):
            self.assertEqual(platform.checked({'platform': pair}), pair)
        for value in (None, {}, {'sdk': 37}, {'sdk': 37, 'lineage': '23.2'},
                      {'sdk': 36, 'lineage': '24.0'}, {'sdk': True, 'lineage': '23.2'},
                      {'sdk': 38, 'lineage': '25.0'}, dict(platform.ANDROID17, extra=True)):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    platform.checked({'platform': value})

    def test_legacy_template_and_retention_identity_are_unchanged(self):
        for path, role in ((INSTALLER, 'installer'), (retention.TEMPLATE, 'retention')):
            original = path.read_bytes()
            self.assertEqual(platform.template(original, {}, role), original)
            self.assertEqual(platform.template(original, {'platform': platform.LEGACY}, role), original)
        config = json.loads((ROOT / 'configs/oem-addons-camera15-23.2.json').read_text())
        spec = config['profiles']['camera']['ota_retention']
        self.assertEqual(spec, retention.expected_spec({}))
        self.assertEqual(retention.KIND, spec['kind'])

    def test_api37_templates_have_strict_source_and_target_guards(self):
        record = {'platform': platform.ANDROID17}
        installer = platform.template(INSTALLER.read_bytes(), record, 'installer')
        hook = retention.template_bytes(record)
        self.assertEqual(installer.count(b'^ro.build.version.sdk=37$'), 1)
        self.assertEqual(hook.count(b'^ro.build.version.sdk=37$'), 2)
        self.assertEqual(hook.count(rb'^ro.lineage.version=24\.0-'), 2)
        self.assertNotIn(b'ro.build.version.sdk=36', installer + hook)
        self.assertNotEqual(retention.expected_spec(record), retention.expected_spec({}))
        self.assertEqual(retention.expected_spec(record)['kind'], retention.KIND37)

    def test_changed_guard_count_requires_review(self):
        original = INSTALLER.read_bytes()
        for modified in (original.replace(b'^ro.build.version.sdk=36$', b'changed'),
                         original + b'\n# ^ro.build.version.sdk=36$\n'):
            with self.assertRaisesRegex(ValueError, 'review required'):
                platform.template(modified, {'platform': platform.ANDROID17}, 'installer')

    @unittest.skipUnless(shutil.which('sh'), 'requires POSIX shell')
    def test_actual_retention_source_generation_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_props = root / 'build.prop'
            product_props = root / 'product.prop'
            product_props.write_text('')
            text = retention.template_bytes({'platform': platform.ANDROID17}).decode()
            checks = [line for line in text.splitlines()
                      if line.lstrip().startswith('grep -q ') and '"$S/build.prop"' in line]
            self.assertEqual(len(checks), 2)
            # Map only the fixed source product-property path into the fixture.
            driver = 'S=$1\nfail() { exit 7; }\n' + '\n'.join(checks).replace(
                '/product/etc/build.prop', '"$2"') + '\n'
            for sdk, lineage, accepted in ((37, '24.0', True), (36, '24.0', False),
                                            (37, '23.2', False)):
                source_props.write_text(f'ro.build.version.sdk={sdk}\nro.lineage.version={lineage}-fixture\n')
                result = subprocess.run(['sh', '-s', str(root), str(product_props)],
                                        input=driver, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode == 0, accepted, result.stderr)

    @unittest.skipUnless(shutil.which('sh'), 'requires POSIX shell')
    def test_actual_installer_rom_guard_rejects_cross_major_and_wrong_rom(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'system').mkdir()
            (root / 'product/etc').mkdir(parents=True)
            (root / 'product/etc/build.prop').write_text('')
            for selected in (platform.LEGACY, platform.ANDROID17):
                text = platform.template(INSTALLER.read_bytes(), {'platform': selected}, 'installer').decode()
                begin = text.index('verify_rom() {')
                function = text[begin:text.index('\n}', begin) + 2]
                for sdk, lineage, accepted in ((selected['sdk'], selected['lineage'], True),
                                                (37 if selected['sdk'] == 36 else 36, selected['lineage'], False),
                                                (selected['sdk'], 'other', False)):
                    with self.subTest(selected=selected, sdk=sdk, lineage=lineage):
                        (root / 'system/build.prop').write_text(
                            f'ro.build.version.sdk={sdk}\nro.lineage.version={lineage}-fixture\n')
                        process = subprocess.run(['sh', '-s', str(root / 'system'), str(root / 'product')],
                            input='SYSTEM=$1\nPRODUCT=$2\ndie() { exit 7; }\n' + function + '\nverify_rom\n',
                            capture_output=True, text=True, timeout=10)
                        self.assertEqual(process.returncode == 0, accepted, process.stderr)


if __name__ == '__main__':
    unittest.main()
