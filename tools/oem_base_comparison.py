"""Compare an add-on's real runtime bindings with baseline/candidate target-files.

Bindings come from the inspected archive through the same descriptor used for
retention. A matching accepted baseline is a prerequisite for interpreting a
candidate difference. No package changes, installation or device operation.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

import app_native_visibility


def compare_bindings(bindings, baseline, candidate):
    if not bindings or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                           for value in bindings.values()):
        raise ValueError('nonempty full-hash runtime bindings required')
    for archive in (baseline, candidate):
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('duplicate target-files members')
    rows = []
    for path, expected in sorted(bindings.items()):
        app_native_visibility.safe_path(path)
        partition, relative = path.split('/', 1)
        if partition not in ('system', 'system_ext', 'product', 'vendor', 'odm'):
            raise ValueError('unsupported runtime partition')
        member = partition.upper() + '/' + relative
        values = {}
        for label, archive in [('baseline', baseline), ('candidate', candidate)]:
            try:
                values[label] = hashlib.sha256(archive.read(member)).hexdigest()
            except KeyError:
                values[label] = None
        rows.append(dict(path=path, expected_sha256=expected,
                         baseline_sha256=values['baseline'],
                         candidate_sha256=values['candidate'],
                         baseline_matches=values['baseline'] == expected,
                         candidate_matches=values['candidate'] == expected))
    return dict(baseline_contract_matches=all(r['baseline_matches'] for r in rows),
                candidate_contract_matches=all(r['candidate_matches'] for r in rows),
                requirements=rows)


def compare(bundle, bundle_sha, profile, baseline, baseline_sha, candidate, candidate_sha):
    # Full archive inspection uses the project's development dependencies. Keep
    # the pure binding comparison usable in the Windows metadata-only checks.
    import oem_ota_compat
    for path, expected in [(bundle, bundle_sha), (baseline, baseline_sha),
                           (candidate, candidate_sha)]:
        app_native_visibility.checked_file(path, expected)
    with zipfile.ZipFile(bundle) as source:
        descriptor = oem_ota_compat.bundle(source, profile)
    with zipfile.ZipFile(baseline) as old, zipfile.ZipFile(candidate) as new:
        report = compare_bindings(descriptor['target_bindings'], old, new)
    report.update(schema=1, created_utc=datetime.now(timezone.utc).isoformat(), profile=profile,
                  tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  descriptor_tool_sha256=hashlib.sha256(Path(oem_ota_compat.__file__).read_bytes()).hexdigest(),
                  interpreter=sys.version,
                  source_bundle_sha256=bundle_sha, baseline_target_files_sha256=baseline_sha,
                  candidate_target_files_sha256=candidate_sha,
                  signature_verified=False, runtime_qualified=False,
                  limits='Exact archive descriptor/manifest and target-file byte comparison. '
                         'Signature verification is separate. A mismatched accepted baseline '
                         'invalidates the comparison assumptions. Changed bytes require '
                         'semantic/ABI review; equal bytes do not prove namespace, lifecycle, '
                         'installation, retention or runtime behavior.')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--bundle-sha256', required=True)
    parser.add_argument('--profile', choices=['camera', 'photos'], required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--baseline-sha256', required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--candidate-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise ValueError('refusing to overwrite audit evidence')
    report = compare(options.bundle, options.bundle_sha256, options.profile,
                     options.baseline, options.baseline_sha256, options.candidate,
                     options.candidate_sha256)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ['baseline_contract_matches',
                     'candidate_contract_matches', 'runtime_qualified']}))
    if not report['baseline_contract_matches']:
        raise SystemExit('Accepted-baseline binding failed; interpret this report as invalid input assumptions.')
