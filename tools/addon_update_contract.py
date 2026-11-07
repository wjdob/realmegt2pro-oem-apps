"""Offline Android update-route prerequisites; never claims successful add-on retention."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import ssl
import zipfile


def certificate_hash(pem):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.decode())).hexdigest()


def inspect(archive, certificate):
    expected = certificate_hash(certificate)
    with zipfile.ZipFile(archive) as z:
        properties = [n for n in z.namelist() if n in ('SYSTEM/build.prop', 'SYSTEM/system/build.prop')]
        if len(properties) != 1:
            raise ValueError('exactly one system build.prop required')
        props = {}
        for line in z.read(properties[0]).decode().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                props[key.strip()] = value.strip()
        if props.get('ro.build.type') not in ('userdebug', 'eng'):
            raise ValueError('automatic backuptool route requires reviewed development build')
        with zipfile.ZipFile(io.BytesIO(z.read('SYSTEM/etc/security/otacerts.zip'))) as trust:
            hashes = [certificate_hash(trust.read(n)) for n in trust.namelist() if not n.endswith('/')]
        if expected not in hashes:
            raise ValueError('project release certificate missing from OTA trust')
        engine = z.read('SYSTEM/bin/update_engine')
        for marker in (b'Running backuptool scripts',
                       b'/postinstall/system/bin/backuptool_postinstall.sh',
                       b'Skipping backuptool scripts'):
            if marker not in engine:
                raise ValueError('compiled update-engine backuptool marker missing')
        members = ['SYSTEM/bin/backuptool_postinstall.sh', 'SYSTEM/bin/backuptool_ab.sh',
                   'SYSTEM/bin/backuptool_ab.functions']
        files = {n: z.read(n) for n in members}
        if b'export ADDOND_VERSION=3' not in files['SYSTEM/bin/backuptool_ab.sh']:
            raise ValueError('V3 add-on backup support missing')
        return {'addon_update_prerequisites_pass': True,
                'packaged_ro_debuggable': props.get('ro.debuggable'),
                'release_certificate_der_sha256': expected,
                'update_engine_sha256': hashlib.sha256(engine).hexdigest(),
                'helpers': {n: hashlib.sha256(data).hexdigest() for n, data in files.items()},
                'limits': 'Packaged prerequisites only. Recheck source mount count, actual update route, '
                          'backups and owner approval; no recovery-path or successful retention claim.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-files', type=Path, required=True)
    parser.add_argument('--certificate', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = inspect(args.target_files, args.certificate.read_bytes())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'addon_update_prerequisites_pass': True}))
