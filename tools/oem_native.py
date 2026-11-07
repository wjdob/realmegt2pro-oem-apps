"""Prepare private profile-local Camera configuration libraries from pinned stock inputs.

No device operations. Preserve the decoder's machine instructions and caller/codec checks;
bind only its loader import to dlopen so the codec stays inside the optional app namespace.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

DECODER_SHA = 'b773ad8a3e5d23ecf08ba9fe6bc683d0a629e1f89af7c18be8dc5f7ae2b0a67e'
LOCAL_DECODER_SHA = '79316d7d22ab945013f87ec5d4984f0506699a9692963f35a60f855a27a9787a'
CODEC_SHA = '24fb1c5834368757ae372699dc701ff00d0c75a7b18b2f5e802b0b2143bd4ddf'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def local_decoder(original):
    if sha(original) != DECODER_SHA:
        raise ValueError('unreviewed stock decoder input')
    data = bytearray(original)
    if data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
        raise ValueError('expected AArch64 ELF64 decoder')
    offset = struct.unpack_from('<Q', data, 40)[0]
    size, count = struct.unpack_from('<HH', data, 58)
    if size != 64:
        raise ValueError('ELF section layout changed')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, offset+i*size) for i in range(count)]
    dynamic = next(s for s in sections if s[1] == 11)
    strings = sections[dynamic[6]]
    versions = next(s for s in sections if s[1] == 0x6fffffff
                    and s[6] == sections.index(dynamic))
    symbols = {}
    for index in range(dynamic[5]//dynamic[9]):
        name = struct.unpack_from('<I', data, dynamic[4]+index*dynamic[9])[0]
        pos = strings[4]+name
        symbols[data[pos:data.index(0, pos)].decode()] = index, pos
    index, pos = symbols['android_load_sphal_library']
    reference, _ = symbols['dlclose']
    old_version = struct.unpack_from('<H', data, versions[4]+2*index)[0]
    new_version = struct.unpack_from('<H', data, versions[4]+2*reference)[0]
    if old_version != 3 or new_version != 4 or 'dlopen' in symbols:
        raise ValueError('expected loader/libdl symbol versions changed')
    old_name = b'android_load_sphal_library'
    data[pos:pos+len(old_name)] = b'dlopen'+b'\0'*(len(old_name)-6)
    struct.pack_into('<H', data, versions[4]+2*index, new_version)
    changes = [i for i, (old, new) in enumerate(zip(original, data)) if old != new]
    if not all(pos <= i < pos+len(old_name)
               or versions[4]+2*index <= i < versions[4]+2*index+2 for i in changes):
        raise ValueError('mutation escaped loader symbol/version scope')
    if len(data) != len(original) or sha(data) != LOCAL_DECODER_SHA:
        raise ValueError('canonical local decoder changed')
    return bytes(data), changes


def prepare(decoder, codec, output):
    original, codec_data = decoder.read_bytes(), codec.read_bytes()
    if sha(codec_data) != CODEC_SHA:
        raise ValueError('unreviewed stock configuration codec input')
    patched, changes = local_decoder(original)
    if output.exists():
        raise ValueError('refusing to overwrite a prepared native profile')
    output.mkdir(parents=True)
    files = {'libOplusStringJNI.so': patched, 'libOplusSecurity.so': codec_data}
    for name, data in files.items():
        (output/name).write_bytes(data)
    report = dict(stock_decoder_sha256=DECODER_SHA, stock_codec_sha256=CODEC_SHA,
                  files={name: dict(sha256=sha(data), bytes=len(data))
                         for name, data in files.items()}, changed_offsets=changes,
                  scope='Optional profile-local codec loading only. Original machine instructions, '
                        'decoding and caller checks retained; no vendor, boot framework or global '
                        'linker policy replacement. Proprietary outputs remain private.')
    (output/'provenance.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--decoder', type=Path, required=True)
    parser.add_argument('--codec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.decoder, args.codec, args.output)))
