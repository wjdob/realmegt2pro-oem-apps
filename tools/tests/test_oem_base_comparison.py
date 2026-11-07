import hashlib
import io
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_base_comparison as audit


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return zipfile.ZipFile(io.BytesIO(stream.getvalue()))


class BaseComparisonTests(unittest.TestCase):
    def test_empty_or_partial_bindings_cannot_produce_a_vacuous_pass(self):
        with archive({}) as old, archive({}) as new:
            for bindings in [{}, {'system/a.jar': 'short'}]:
                with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                    audit.compare_bindings(bindings, old, new)

    def test_oplus_framework_is_distinct_from_aosp_framework(self):
        baseline = {'SYSTEM/framework/oplus-fwk.jar': b'OEM',
                    'SYSTEM/framework/framework.jar': b'AOSP'}
        candidate = dict(baseline, **{'SYSTEM/framework/oplus-fwk.jar': b'OEM37'})
        with archive(baseline) as old, archive(candidate) as new:
            result = audit.compare_bindings(
                {'system/framework/oplus-fwk.jar': hashlib.sha256(b'OEM').hexdigest()}, old, new)
        self.assertTrue(result['baseline_contract_matches'])
        self.assertFalse(result['candidate_contract_matches'])
        self.assertEqual(result['requirements'][0]['path'], 'system/framework/oplus-fwk.jar')

    def test_wrong_provider_mapping_cannot_pass_the_accepted_baseline(self):
        files = {'SYSTEM/framework/oplus-fwk.jar': b'OEM',
                 'SYSTEM/framework/framework.jar': b'AOSP'}
        with archive(files) as old, archive(files) as new:
            result = audit.compare_bindings(
                {'system/framework/framework.jar': hashlib.sha256(b'OEM').hexdigest()}, old, new)
        self.assertFalse(result['baseline_contract_matches'])

    def test_missing_candidate_provider_is_explicit(self):
        with archive({'ODM/lib64/provider.so': b'provider'}) as old, archive({}) as new:
            result = audit.compare_bindings(
                {'odm/lib64/provider.so': hashlib.sha256(b'provider').hexdigest()}, old, new)
        self.assertTrue(result['baseline_contract_matches'])
        self.assertFalse(result['candidate_contract_matches'])
        self.assertIsNone(result['requirements'][0]['candidate_sha256'])

    def test_missing_baseline_provider_invalidates_assumptions(self):
        with archive({}) as old, archive({'ODM/lib64/provider.so': b'provider'}) as new:
            result = audit.compare_bindings(
                {'odm/lib64/provider.so': hashlib.sha256(b'provider').hexdigest()}, old, new)
        self.assertFalse(result['baseline_contract_matches'])
        self.assertTrue(result['candidate_contract_matches'])

    def test_equal_bytes_are_only_contract_equality(self):
        files = {'VENDOR/etc/config.xml': b'config'}
        with archive(files) as old, archive(files) as new:
            result = audit.compare_bindings(
                {'vendor/etc/config.xml': hashlib.sha256(b'config').hexdigest()}, old, new)
        self.assertTrue(result['baseline_contract_matches'])
        self.assertTrue(result['candidate_contract_matches'])
        self.assertNotIn('runtime_qualified', result)


if __name__ == '__main__':
    unittest.main()
