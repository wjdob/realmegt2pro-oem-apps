"""Verify add-on failure propagation inputs; native/OTA acceptance is separate."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import addon_update_contract

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'configs/addon-failure-propagation-23.2.json'
ENGINE = 'system/update_engine/payload_consumer/postinstall_runner_action.cc'


def mount_function(text):
    start = text.index('bool PostinstallRunnerAction::MountPartition(')
    end = text.index('\nvoid PostinstallRunnerAction::PerformPartitionPostinstall()', start)
    return text[start:end]


def check_source(tree):
    contract = json.loads(CONTRACT.read_text())
    rows = []
    for row in contract['files']:
        data = (Path(tree) / row['path']).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != row['after_sha256']:
            raise ValueError('failure-propagation source differs: ' + row['path'])
        rows.append({'path': row['path'], 'sha256': digest})
    function = mount_function((Path(tree) / ENGINE).read_text())
    if hashlib.sha256(function.encode()).hexdigest() != contract['mount_function_sha256']:
        raise ValueError('reviewed mount function differs')
    return {'failure_propagation_source_pass': True, 'files': rows,
            'limits': 'Exact source only; full native compile and real OTA failure handling untested.'}


def check_package(target_files, certificate):
    contract = json.loads(CONTRACT.read_text())
    # Preserve the existing trust/build/V3 route prerequisites as a separate claim.
    prerequisites = addon_update_contract.inspect(target_files, certificate)
    with zipfile.ZipFile(target_files) as archive:
        names = archive.namelist()
        for name in ('SYSTEM/bin/update_engine', 'SYSTEM/bin/backuptool_ab.sh',
                     'SYSTEM/bin/backuptool_postinstall.sh'):
            if names.count(name) != 1:
                raise ValueError('exactly one packaged failure component required: ' + name)
        for row in contract['files']:
            if row['path'] == ENGINE:
                continue
            member = 'SYSTEM/bin/' + Path(row['path']).name
            if hashlib.sha256(archive.read(member)).hexdigest() != row['after_sha256']:
                raise ValueError('packaged failure helper differs: ' + member)
        if contract['engine_marker'].encode() not in archive.read('SYSTEM/bin/update_engine'):
            raise ValueError('compiled failure-propagation marker missing')
        if contract['engine_scope_marker'].encode() not in archive.read('SYSTEM/bin/update_engine'):
            raise ValueError('compiled system-scope marker missing')
    return {'failure_propagation_package_prerequisites_pass': True,
            'update_route_prerequisites': prerequisites,
            'limits': 'Helper hashes and compiled markers are prerequisites, not semantic proof. '
                      'The updater of the SOURCE OS executes this path; a corrected target alone '
                      'does not fix an older installed updater. Native/real OTA and data retention untested.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--source-tree', type=Path)
    group.add_argument('--target-files', type=Path)
    parser.add_argument('--certificate', type=Path, default=ROOT / 'certs/rmx3301-releasekey.x509.pem')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = check_source(args.source_tree) if args.source_tree else check_package(args.target_files, args.certificate.read_bytes())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
