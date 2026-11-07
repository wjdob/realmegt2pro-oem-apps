"""Prepare pinned APS normal-linker provider discovery; never contacts a device."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT/'configs/oem-camera-filter-capture-23.2.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def branch_target(word, pc, bits, shift):
    value = (word >> shift) & ((1 << bits)-1)
    if value & (1 << (bits-1)):
        value -= 1 << bits
    return pc+(value << 2)


def route_provider(data, contract):
    if len(data) > 64*1024**2 or sha(data) != contract['input_sha256']:
        raise ValueError('unreviewed APS filter input')
    if (len(data) < 64 or data[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<HH', data, 16) != (3, 183)):
        raise ValueError('expected little-endian ARM64 shared ELF')
    phoff, shoff = struct.unpack_from('<QQ', data, 32)
    phsize, phnum, shsize, shnum = struct.unpack_from('<HHHH', data, 54)
    if (phsize != 56 or shsize != 64 or not phnum or not shnum
            or phoff+phsize*phnum > len(data) or shoff+shsize*shnum > len(data)):
        raise ValueError('invalid ELF tables')
    programs = [struct.unpack_from('<IIQQQQQQ', data, phoff+i*phsize)
                for i in range(phnum)]
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, shoff+i*shsize)
                for i in range(shnum)]
    if any(p[0] == 1 and (p[2]+p[5] > len(data) or p[6] < p[5]) for p in programs):
        raise ValueError('invalid load segment bounds')
    if any(s[1] != 8 and s[4]+s[5] > len(data) for s in sections):
        raise ValueError('invalid section bounds')

    def offset(va, size, flags):
        owners = [p for p in programs if p[0] == 1 and p[1] == flags
                  and p[3] <= va and va+size <= p[3]+p[5]]
        if len(owners) != 1:
            raise ValueError('routing target outside exact segment permissions')
        p = owners[0]
        return p[2]+va-p[3]

    old = contract['old_path'].encode()+b'\0'
    if old != b'/odm/lib64/libmsnativefilter.so\0' or data.count(old) != 1:
        raise ValueError('old provider literal differs')
    soname = contract['new_soname']
    if soname != 'libmsnativefilter.so':
        raise ValueError('unreviewed normal-linker provider')
    literal = offset(contract['old_path_va'], len(old), 4)
    if data[literal:literal+len(old)] != old:
        raise ValueError('provider literal address differs')
    new = (soname.encode()+b'\0').ljust(len(old), b'\0')
    result = bytearray(data)
    result[literal:literal+len(old)] = new
    probe = contract['probe']
    flags_pc, call_pc, branch_pc = (probe[k] for k in ['flags_va', 'call_va', 'branch_va'])
    if call_pc != flags_pc+4 or branch_pc != call_pc+12:
        raise ValueError('unreviewed provider probe layout')
    begin = offset(flags_pc, branch_pc-flags_pc+4, 5)
    flags, call, branch = (struct.unpack_from('<I', data, offset(pc, 4, 5))[0]
                           for pc in [flags_pc, call_pc, branch_pc])
    if flags != 0x2a1f03e1:
        raise ValueError('expected original F_OK access flags')
    if call & 0xfc000000 != 0x94000000 or branch_target(call, call_pc, 26, 0) != probe['access_plt_va']:
        raise ValueError('expected original access call')
    if branch & 0xff00001f != 0x34000000 or branch_target(branch, branch_pc, 19, 5) != probe['success_va']:
        raise ValueError('expected original zero-int success branch')
    offset(probe['dlopen_plt_va'], 4, 5)
    delta = probe['dlopen_plt_va']-call_pc
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError('dlopen outside BL range')
    # Real loader capability, not a fabricated presence result. Keep the one
    # reference for the existing cached factory/process lifetime. The original
    # feature flag/fallback and normal loader's namespace checks stay active.
    struct.pack_into('<I', result, offset(flags_pc, 4, 5), 0x52800021)  # RTLD_LAZY
    struct.pack_into('<I', result, offset(call_pc, 4, 5), 0x94000000 | ((delta >> 2) & 0x3ffffff))
    struct.pack_into('<I', result, offset(branch_pc, 4, 5), (branch & 0x00ffffff) | 0xb5000000)
    allowed = [(literal, literal+len(old)), (begin, begin+8),
               (offset(branch_pc, 4, 5), offset(branch_pc, 4, 5)+4)]
    if any(not any(a <= i < z for a, z in allowed)
           for i, (a, b) in enumerate(zip(data, result)) if a != b):
        raise ValueError('mutation escaped provider discovery scope')
    if len(result) != len(data) or sha(result) != contract['output_sha256']:
        raise ValueError('canonical capture provider route differs')
    patterns = []
    for a, z in [(literal, literal+len(old)), (begin, begin+branch_pc-flags_pc+4)]:
        before, after = data[a:z], bytes(result[a:z])
        if data.count(before) != 1:
            raise ValueError('recipe replacement pattern is ambiguous')
        patterns.append(dict(old_hex=before.hex(), new_hex=after.hex(), expected_count=1))
    return bytes(result), patterns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not any(output.is_relative_to(ROOT/d) for d in ['tmp', '.local']):
        parser.error('use a new private workspace output directory')
    contract = json.loads(CONTRACT.read_text())
    data, patterns = route_provider(args.native.read_bytes(), contract)
    output.mkdir(parents=True, exist_ok=False)
    (output/'libAlgoInterface.so').write_bytes(data)
    report = dict(pass_all=True, input_sha256=contract['input_sha256'],
                  output_sha256=sha(data), bytes=len(data), recipe_actions=patterns,
                  limits='Offline discovery correction only; namespace and actual capture acceptance pending.')
    (output/'preparation.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(pass_all=True, output_sha256=sha(data))))


if __name__ == '__main__':
    main()
