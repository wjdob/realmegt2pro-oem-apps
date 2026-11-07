import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import addon_failure_contract as contract
import test_addon_update_contract as route_tests


class PackageFailureTests(unittest.TestCase):
    def fixture(self, *, marker=True, scope_marker=True, helper=True, duplicate=False):
        before, cert = route_tests.AddonContractTests().fixture()
        spec = json.loads(contract.CONTRACT.read_text())
        helpers = {row['path']: ('reviewed:' + row['path']).encode()
                   for row in spec['files'] if row['path'] != contract.ENGINE}
        for row in spec['files']:
            if row['path'] in helpers:
                row['after_sha256'] = hashlib.sha256(helpers[row['path']]).hexdigest()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        spec_path = Path(temp.name) / 'contract.json'
        spec_path.write_text(json.dumps(spec))
        p = patch.object(contract, 'CONTRACT', spec_path)
        p.start(); self.addCleanup(p.stop)
        output = io.BytesIO()
        with zipfile.ZipFile(before) as src, zipfile.ZipFile(output, 'w') as dst:
            for name in src.namelist():
                data = src.read(name)
                if name == 'SYSTEM/bin/update_engine' and marker:
                    data += spec['engine_marker'].encode()
                if name == 'SYSTEM/bin/update_engine' and scope_marker:
                    data += spec['engine_scope_marker'].encode()
                for rel, content in helpers.items():
                    if name == 'SYSTEM/bin/' + Path(rel).name and helper:
                        data = content + (b'\nexport ADDOND_VERSION=3' if name.endswith('backuptool_ab.sh') else b'')
                        row = next(r for r in spec['files'] if r['path'] == rel)
                        row['after_sha256'] = hashlib.sha256(data).hexdigest()
                dst.writestr(name, data)
            if duplicate:
                dst.writestr('SYSTEM/bin/backuptool_postinstall.sh', b'duplicate')
        spec_path.write_text(json.dumps(spec))
        output.seek(0)
        return output, cert

    def test_current_contract_components(self):
        self.assertTrue(contract.check_package(*self.fixture())['failure_propagation_package_prerequisites_pass'])

    def test_old_engine_marker_is_insufficient(self):
        with self.assertRaisesRegex(ValueError, 'failure-propagation marker missing'):
            contract.check_package(*self.fixture(marker=False))

    def test_unscoped_failure_binary_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'system-scope marker missing'):
            contract.check_package(*self.fixture(scope_marker=False))

    def test_old_helpers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'helper differs'):
            contract.check_package(*self.fixture(helper=False))

    def test_duplicate_helper_is_rejected(self):
        with self.assertWarns(UserWarning):
            fixture = self.fixture(duplicate=True)
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            contract.check_package(*fixture)


@unittest.skipUnless(shutil.which('c++'), 'C++ host compiler unavailable')
class ActualMountFunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        native = Path(__file__).parent / 'native'
        spec = json.loads(contract.CONTRACT.read_text())
        body = (native / 'BackuptoolMountPartition.cpp.inc').read_text()
        body = body[body.index('bool PostinstallRunnerAction::MountPartition('):]
        if hashlib.sha256(body.encode()).hexdigest() != spec['mount_function_sha256']:
            raise AssertionError('compiled fixture differs from reviewed full source function')
        cls.binaries = {}
        for mode, flags in [('android', ['-D__ANDROID__', '-DRUN_BACKUPTOOL']),
                            ('recovery', ['-D__ANDROID__', '-D__ANDROID_RECOVERY__', '-DRUN_BACKUPTOOL']),
                            ('no-backuptool', ['-D__ANDROID__'])]:
            target = Path(cls.temp.name) / mode
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', *flags,
                            str(native / 'BackuptoolMountHarness.cpp'), '-o', str(target)],
                           check=True, capture_output=True, timeout=60)
            cls.binaries[mode] = target

    def run_case(self, *, wait=0, mounts=1, refuse_rw=0, set_context=0,
                 clear_context=0, unmount=0, readonly=0, mode='android',
                 partition='system', filesystem='ext4', read_mode=0,
                 refuse_source=0, source_slot=0):
        result = subprocess.run([str(self.binaries[mode]), *map(str, [wait, mounts, refuse_rw,
                                set_context, clear_context, unmount, readonly]),
                                partition, filesystem, str(read_mode), str(refuse_source), str(source_slot)],
                                check=True, capture_output=True, text=True, timeout=5)
        return json.loads(result.stdout)

    def test_success(self):
        row = self.run_case()
        self.assertTrue(row['mount_result'] and row['readonly_at_end'] and row['context_cleared'])
        self.assertEqual(row['script_calls'], 1)

    def test_helper_failures_stop_after_cleanup(self):
        for status in (7 << 8, -1, 9, 0x7f):
            with self.subTest(status=status):
                row = self.run_case(wait=status)
                self.assertFalse(row['mount_result'])
                self.assertTrue(row['readonly_at_end'] and row['context_cleared'])
                self.assertEqual(row['ro_mounts'], 0)

    def test_plain_profile_never_unmounts_an_absent_rw_mount(self):
        row = self.run_case(mounts=0)
        self.assertTrue(row['mount_result'] and row['readonly_at_end'])
        self.assertEqual(row['unmount_calls'], 0)
        self.assertEqual(row['script_calls'], 0)

    def test_refused_mount_stops_before_helper_and_restores_readonly(self):
        row = self.run_case(refuse_rw=1)
        self.assertFalse(row['mount_result'])
        self.assertTrue(row['readonly_at_end'])
        self.assertEqual(row['script_calls'], 0)

    def test_context_setup_failure_stops_before_helper(self):
        row = self.run_case(set_context=1)
        self.assertFalse(row['mount_result'])
        self.assertTrue(row['readonly_at_end'] and row['context_cleared'])
        self.assertEqual(row['script_calls'], 0)

    def test_context_reset_failure_stops_and_restores_readonly(self):
        row = self.run_case(clear_context=1)
        self.assertFalse(row['mount_result'])
        self.assertTrue(row['readonly_at_end'])

    def test_unmount_failure_stops_and_restores_readonly(self):
        row = self.run_case(unmount=1)
        self.assertFalse(row['mount_result'])
        self.assertTrue(row['readonly_at_end'])

    def test_readonly_reset_failure_stops(self):
        self.assertFalse(self.run_case(readonly=1)['mount_result'])

    def test_recovery_and_disabled_backuptool_do_not_run_hooks(self):
        for mode in ('recovery', 'no-backuptool'):
            with self.subTest(mode=mode):
                row = self.run_case(wait=7 << 8, mode=mode)
                self.assertTrue(row['mount_result'])
                self.assertEqual(row['script_calls'], 0)

    def test_erofs_vendor_does_not_interpret_uuid_as_a_mount_counter(self):
        #548 is measured at ext4's count offset in the saved real vendor EROFS.
        row = self.run_case(partition='vendor', filesystem='erofs', mounts=548, refuse_rw=1)
        self.assertTrue(row['mount_result'] and row['readonly_at_end'])
        self.assertEqual((row['source_queries'], row['source_reads'], row['rw_mounts'], row['script_calls']), (0,0,0,0))
        self.assertEqual(row['ro_mounts'], 1)

    def test_other_partitions_and_target_filesystems_skip_helper_only(self):
        for partition, filesystem in [('vendor','ext4'), ('product','ext4'), ('system','erofs')]:
            with self.subTest(partition=partition, filesystem=filesystem):
                row = self.run_case(partition=partition, filesystem=filesystem, refuse_rw=1)
                self.assertTrue(row['mount_result'])
                self.assertEqual((row['source_queries'], row['source_reads'], row['rw_mounts'], row['script_calls']), (0,0,0,0))
                self.assertEqual(row['ro_mounts'], 1)

    def test_source_resolution_failure_stops_without_modifying_target(self):
        row = self.run_case(refuse_source=1)
        self.assertFalse(row['mount_result'])
        self.assertTrue(row['readonly_at_end'])
        self.assertEqual((row['source_queries'], row['source_reads'], row['rw_mounts'], row['script_calls']), (1,0,0,0))

    def test_unreadable_or_malformed_ext4_probe_fails_closed(self):
        for read_mode in (1,2,4):
            with self.subTest(read_mode=read_mode):
                row = self.run_case(read_mode=read_mode)
                self.assertFalse(row['mount_result'])
                self.assertTrue(row['readonly_at_end'])
                self.assertEqual((row['source_reads'], row['rw_mounts'], row['script_calls']), (1,0,0))

    def test_non_ext4_source_has_no_ext4_counter(self):
        row = self.run_case(mounts=548, read_mode=3, refuse_rw=1)
        self.assertTrue(row['mount_result'])
        self.assertEqual((row['source_reads'], row['rw_mounts'], row['script_calls']), (1,0,0))

    def test_fresh_install_with_invalid_source_slot_skips_source_probe(self):
        row = self.run_case(source_slot=-1)
        self.assertTrue(row['mount_result'])
        self.assertEqual((row['source_queries'], row['source_reads'], row['script_calls']), (0,0,0))

    def test_mount_counter_is_decoded_as_little_endian(self):
        for mounts in (1,256,65535):
            with self.subTest(mounts=mounts):
                row = self.run_case(mounts=mounts)
                self.assertTrue(row['mount_result'])
                self.assertEqual((row['source_reads'], row['rw_mounts'], row['script_calls']), (1,1,1))
