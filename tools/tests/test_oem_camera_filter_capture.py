"""Provider discovery must use the real normal-loader result and bounded edits."""
import copy
import struct
import unittest
import oem_camera_filter_capture as capture


class CaptureProviderDiscoveryTests(unittest.TestCase):
    def setUp(self):
        data = bytearray(65536)
        data[:16] = b'\x7fELF\x02\x01\x01'+bytes(9)
        struct.pack_into('<HHIQQQIHHHHHH', data, 16,
                         3, 183, 1, 0, 64, 0xf000, 0, 64, 56, 2, 64, 3, 0)
        struct.pack_into('<IIQQQQQQ', data, 64, 1, 4, 0, 0, 0, 0x1800, 0x1800, 4096)
        struct.pack_into('<IIQQQQQQ', data, 120, 1, 5, 0x4000, 0x4000, 0x4000,
                         0x2000, 0x2000, 4096)
        struct.pack_into('<IIQQQQIIQQ', data, 0xf040,
                         0, 1, 2, 0x200, 0x200, 0x1600, 0, 0, 8, 0)
        struct.pack_into('<IIQQQQIIQQ', data, 0xf080,
                         0, 1, 6, 0x4000, 0x4000, 0x2000, 0, 0, 4, 0)
        old = b'/odm/lib64/libmsnativefilter.so\0'
        data[0x300:0x300+len(old)] = old
        # F_OK, BL access@0x4800, two untouched instructions, CBZ w0 ->0x4184.
        struct.pack_into('<IIIII', data, 0x4100, 0x2a1f03e1, 0x940001bf,
                         0xd503201f, 0xd503201f, 0x340003a0)
        marker = b'original feature/cache/fallback!!'
        data[0x900:0x900+len(marker)] = marker
        self.assertEqual(len(data), 65536)
        self.data = bytes(data)
        expected = bytearray(data)
        expected[0x300:0x300+len(old)] = b'libmsnativefilter.so\0'.ljust(len(old), b'\0')
        struct.pack_into('<II', expected, 0x4100, 0x52800021, 0x940001ff)
        struct.pack_into('<I', expected, 0x4110, 0xb50003a0)
        self.expected = bytes(expected)
        self.contract = dict(input_sha256=capture.sha(self.data),
                             output_sha256=capture.sha(self.expected),
                             old_path=old[:-1].decode(), old_path_va=0x300,
                             new_soname='libmsnativefilter.so',
                             probe=dict(flags_va=0x4100, call_va=0x4104,
                                        branch_va=0x4110, success_va=0x4184,
                                        access_plt_va=0x4800, dlopen_plt_va=0x4900))

    def reject(self, data, contract, reason):
        contract['input_sha256'] = capture.sha(data)
        with self.assertRaisesRegex(ValueError, reason):
            capture.route_provider(bytes(data), contract)

    def test_normal_loader_result_and_recipe_reconstruction(self):
        result, actions = capture.route_provider(self.data, self.contract)
        self.assertEqual(result, self.expected)
        self.assertEqual(result[:4096], self.expected[:4096])
        self.assertEqual(result[:0x300], self.data[:0x300])  # ELF headers unchanged.
        self.assertEqual(result[0x900:0x920], self.data[0x900:0x920])
        self.assertEqual(struct.unpack_from('<I', result, 0x4100)[0], 0x52800021)
        call = struct.unpack_from('<I', result, 0x4104)[0]
        self.assertEqual(capture.branch_target(call, 0x4104, 26, 0), 0x4900)
        branch = struct.unpack_from('<I', result, 0x4110)[0]
        self.assertEqual(branch >> 24, 0xb5)  # Non-null64-bit handle, not low32 bits.
        self.assertEqual(capture.branch_target(branch, 0x4110, 19, 5), 0x4184)
        rebuilt = self.data
        for action in actions:
            old, new = bytes.fromhex(action['old_hex']), bytes.fromhex(action['new_hex'])
            self.assertEqual(rebuilt.count(old), 1)
            rebuilt = rebuilt.replace(old, new)
        self.assertEqual(rebuilt, result)

    def test_signed_backward_branch_decoding(self):
        self.assertEqual(capture.branch_target(0x97ffffbf, 0x4104, 26, 0), 0x4000)

    def test_wrong_input_hash(self):
        c = copy.deepcopy(self.contract); c['input_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'unreviewed'):
            capture.route_provider(self.data, c)

    def test_wrong_machine(self):
        b = bytearray(self.data); struct.pack_into('<H', b, 18, 62)
        self.reject(b, copy.deepcopy(self.contract), 'ARM64')

    def test_invalid_program_bounds(self):
        b = bytearray(self.data); struct.pack_into('<Q', b, 64+32, 999999)
        self.reject(b, copy.deepcopy(self.contract), 'load segment bounds')

    def test_invalid_section_bounds(self):
        b = bytearray(self.data); struct.pack_into('<Q', b, 0xf040+32, 999999)
        self.reject(b, copy.deepcopy(self.contract), 'section bounds')

    def test_literal_requires_readonly_nonexecutable_storage(self):
        b = bytearray(self.data); struct.pack_into('<I', b, 68, 5)
        self.reject(b, copy.deepcopy(self.contract), 'segment permissions')

    def test_wrong_flags(self):
        b = bytearray(self.data); struct.pack_into('<I', b, 0x4100, 0x52800041)
        self.reject(b, copy.deepcopy(self.contract), 'F_OK')

    def test_wrong_original_call(self):
        b = bytearray(self.data); struct.pack_into('<I', b, 0x4104, 0x940001c0)
        self.reject(b, copy.deepcopy(self.contract), 'access call')

    def test_wrong_success_branch(self):
        b = bytearray(self.data); struct.pack_into('<I', b, 0x4110, 0x350003a0)
        self.reject(b, copy.deepcopy(self.contract), 'zero-int')

    def test_duplicate_literal(self):
        b = bytearray(self.data); old = self.contract['old_path'].encode()+b'\0'
        b[0xb00:0xb00+len(old)] = old
        self.reject(b, copy.deepcopy(self.contract), 'literal')

    def test_no_arbitrary_provider_or_path(self):
        c = copy.deepcopy(self.contract); c['new_soname'] = '/data/local/tmp/other.so'
        self.reject(self.data, c, 'normal-linker provider')

    def test_probe_layout_change(self):
        c = copy.deepcopy(self.contract); c['probe']['branch_va'] += 4
        self.reject(self.data, c, 'layout')

    def test_wrong_canonical_output(self):
        c = copy.deepcopy(self.contract); c['output_sha256'] = '0'*64
        self.reject(self.data, c, 'canonical')


if __name__ == '__main__':
    unittest.main()
