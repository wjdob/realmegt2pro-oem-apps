"""Prepare/check the pinned stock dependency used by the vendor capture filter."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'configs/camera-vendor-filter-23.2.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def provider_bytes(data, contract):
    p = contract['provider']
    if len(data) != p['bytes'] or sha(data) != p['sha256']:
        raise ValueError('stock vendor filter provider differs')
    if data[:6] != b'\x7fELF\x02\x01':
        raise ValueError('expected ARM64 ELF vendor provider')


def prepare(source, root=ROOT, contract=None):
    contract = contract or json.loads(CONTRACT.read_text())
    data = source.read_bytes()
    provider_bytes(data, contract)
    target = root / contract['provider']['source']
    if not target.resolve().is_relative_to((root / '.local/build-inputs').resolve()):
        raise ValueError('provider input must remain private under .local/build-inputs')
    if any(p.is_symlink() for p in [target, *target.parents] if p != root):
        raise ValueError('symlink in private provider destination')
    existed = target.exists()
    if existed:
        if target.read_bytes() != data:
            raise ValueError('existing private provider differs; preserve it')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
    return dict(prepared=True, reused=existed, provider_sha256=sha(target.read_bytes()))


def check_pair(provider, aps, contract):
    provider_bytes(provider, contract)
    if sha(aps) != contract['aps']['sha256']:
        raise ValueError('APS filter route differs from original vendor-path contract')
    literal = contract['aps']['provider_literal'].encode() + b'\0'
    if aps.count(literal) != 1:
        raise ValueError('original vendor provider literal missing or ambiguous')


def check_source(tree, contract=None):
    contract = contract or json.loads(CONTRACT.read_text())
    p = contract['provider']
    check_pair((tree/p['path']).read_bytes(), (tree/contract['aps']['path']).read_bytes(), contract)
    listing = (tree/'device/realme/ferrari/proprietary-files.txt').read_text().splitlines()
    if listing.count(p['stock_path']) != 1:
        raise ValueError('provider extraction entry missing or ambiguous')
    bp = (tree/'vendor/realme/ferrari/Android.bp').read_text()
    blocks = [s for s in bp.split('cc_prebuilt_library_shared {')
              if re.search(r'\bname:\s*"'+re.escape(p['module'])+r'"', s)]
    if len(blocks) != 1:
        raise ValueError('vendor provider module missing or ambiguous')
    block = blocks[0].split('\n}\n', 1)[0]
    if ('device_specific: true' not in block or 'compile_multilib: "64"' not in block
            or '"proprietary/'+p['stock_path']+'"' not in block):
        raise ValueError('provider must retain ARM64 ODM placement')
    needed = re.search(r'shared_libs:\s*\[(.*?)\]', block, re.S)
    if needed is None or set(re.findall(r'"([^"]+)"', needed[1])) != set(p['shared_libs']):
        raise ValueError('vendor provider dependencies differ')
    mk = (tree/'vendor/realme/ferrari/ferrari-vendor.mk').read_text()
    if len(re.findall(r'^\s*'+re.escape(p['module'])+r'\s*\\?\s*$', mk, re.M)) != 1:
        raise ValueError('provider product selection missing or ambiguous')
    return dict(pass_all=True, scope='Source/extraction/module/dependency contract only', provider_sha256=p['sha256'], aps_sha256=contract['aps']['sha256'])


def check_package(target_files, contract=None):
    contract = contract or json.loads(CONTRACT.read_text())
    with zipfile.ZipFile(target_files) as archive:
        members = archive.namelist()
        required = [contract[k]['target_files'] for k in ('provider', 'aps')]
        if any(members.count(n) != 1 for n in required):
            raise ValueError('packaged vendor filter dependency missing or ambiguous')
        check_pair(*(archive.read(n) for n in required), contract)
    return dict(pass_all=True, scope='Packaged vendor provider/APS bytes only; no live image acceptance', provider_sha256=contract['provider']['sha256'], aps_sha256=contract['aps']['sha256'])


def new_report(path, root=ROOT):
    """Allow declared output roots, including shared controller run records.

    A detached source export can link .local/goals/runs to its controller.
    Resolve that narrow root too; unrelated nested links must not expand access.
    """
    report = path.resolve()
    roots = [(root/p).resolve() for p in ('tmp', '.local', '.local/goals/runs')]
    if report.exists() or not any(report.is_relative_to(p) for p in roots):
        raise ValueError('use a new report under tmp or .local')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare-provider', type=Path)
    mode.add_argument('--source-tree', type=Path)
    mode.add_argument('--target-files', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = new_report(args.report)
    except ValueError as error:
        parser.error(str(error))
    result = (prepare(args.prepare_provider) if args.prepare_provider else
              check_source(args.source_tree) if args.source_tree else check_package(args.target_files))
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x') as stream:
        stream.write(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
