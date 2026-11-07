"""Guard the SDK LUT route; the proprietary SDK is never a committed fixture."""
import hashlib
import struct
import unittest
import zlib

import oem_camera_filter_sdk as tool


def fixture(path):
    raw = bytearray(116)
    raw[:8] = b'dex\n035\0'
    struct.pack_into('<II', raw, 56, 1, 112)
    struct.pack_into('<I', raw, 112, 116)
    raw.extend(bytes([len(path)]) + path + b'\0')
    struct.pack_into('<I', raw, 32, len(raw))
    raw[12:32] = hashlib.sha1(raw[32:]).digest()
    struct.pack_into('<I', raw, 8, zlib.adler32(raw[12:]) & 0xffffffff)
    return bytes(raw)


class FilterSdkTests(unittest.TestCase):
    def setUp(self):
        self.old = '/odm/etc/camera/meishe_lut/'
        self.new = '/system/etc/realme-lut/'
        self.data = fixture(self.old.encode())
        self.contract = dict(input_dex_sha256=tool.sha(self.data),
                             old_path=self.old, new_path=self.new)

    def test_intermediate_retains_offsets_and_has_valid_checksums(self):
        result = tool.replace_path(self.data, self.contract)
        self.assertEqual(len(result), len(self.data))
        self.assertEqual([x[4] for x in tool.strings(result)], [self.new.encode()])
        self.assertEqual(result[12:32], hashlib.sha1(result[32:]).digest())
        self.assertEqual(struct.unpack_from('<I', result, 8)[0], zlib.adler32(result[12:]))
        self.assertIn(self.old.encode(), self.data)

    def test_unknown_input_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unreviewed'):
            tool.replace_path(self.data + b'changed', self.contract)

    def test_reviewed_hash_does_not_hide_bad_dex_integrity(self):
        bad = bytearray(self.data)
        bad[12] ^= 1
        contract = {**self.contract, 'input_dex_sha256': tool.sha(bad)}
        with self.assertRaisesRegex(ValueError, 'unreviewed'):
            tool.replace_path(bytes(bad), contract)

    def test_missing_literal_rejected(self):
        with self.assertRaisesRegex(ValueError, 'one exact'):
            tool.replace_path(self.data, {**self.contract, 'old_path': '/another/path/'})

    def test_expanding_or_embedded_null_path_rejected(self):
        for value in ['/' + 'a' * 100, '/system/\0path/']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                tool.replace_path(self.data, {**self.contract, 'new_path': value})


if __name__ == '__main__':
    unittest.main()
