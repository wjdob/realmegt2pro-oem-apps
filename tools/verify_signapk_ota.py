"""Verify an Android SignApk whole-file OTA signature without copying the OTA."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes


MESSAGE = b'signed by SignApk\0'
OTACERT = 'META-INF/com/android/otacert'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_cert(data):
    if data.startswith(b'-----BEGIN CERTIFICATE-----'):
        return x509.load_pem_x509_certificate(data)
    return x509.load_der_x509_certificate(data)


def signer_cert(ota, archive, scratch, openssl):
    comment = archive.comment
    if len(comment) < len(MESSAGE) + 7 or not comment.startswith(MESSAGE):
        raise ValueError('ZIP comment is not an Android SignApk signature')
    offset_from_end = int.from_bytes(comment[-6:-4], 'little')
    marker = int.from_bytes(comment[-4:-2], 'little')
    total = int.from_bytes(comment[-2:], 'little')
    signature_start = len(comment) - offset_from_end
    signature_end = len(comment) - 6
    if marker != 0xffff or total != len(comment) or signature_start != len(MESSAGE):
        raise ValueError('invalid SignApk ZIP-comment footer')
    signature = comment[signature_start:signature_end]
    if not signature.startswith(b'\x30'):
        raise ValueError('CMS signature block is not DER encoded')

    signed_bytes = ota.stat().st_size - len(comment) - 2
    if signed_bytes <= 0:
        raise ValueError('invalid signed ZIP length')
    with ota.open('rb') as stream:
        stream.seek(-len(comment) - 2, 2)
        comment_length = int.from_bytes(stream.read(2), 'little')
    if comment_length != len(comment):
        raise ValueError('EOCD comment length differs from ZIP parser')

    embedded = load_cert(archive.read(OTACERT))
    with tempfile.TemporaryDirectory(prefix='verify-signapk-', dir=scratch) as temp:
        temp = Path(temp)
        cms_path = temp / 'signature.der'
        signer_path = temp / 'signer.pem'
        cms_path.write_bytes(signature)
        command = [openssl, 'cms', '-verify', '-binary', '-inform', 'DER',
                   '-in', str(cms_path), '-content', '/dev/stdin', '-noverify',
                   '-signer', str(signer_path), '-out', '/dev/null']
        process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            with ota.open('rb') as stream:
                remaining = signed_bytes
                while remaining:
                    block = stream.read(min(4 * 1024 * 1024, remaining))
                    if not block:
                        raise ValueError('truncated signed OTA data')
                    process.stdin.write(block)
                    remaining -= len(block)
            process.stdin.close()
            stderr = process.stderr.read().decode(errors='replace')
            code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        if code:
            raise ValueError('OpenSSL CMS verification failed: ' + stderr.strip())
        signer = load_cert(signer_path.read_bytes())
    signer_sha = signer.fingerprint(hashes.SHA256()).hex()
    embedded_sha = embedded.fingerprint(hashes.SHA256()).hex()
    return {
        'cms_sha256': hashlib.sha256(signature).hexdigest(),
        'signed_bytes': signed_bytes,
        'embedded_otacert_sha256': embedded_sha,
        'cms_signer_sha256': signer_sha,
        'signer_matches_otacert': signer_sha == embedded_sha,
        'chain_validation': 'disabled; signer identity compared with embedded otacert',
        'openssl': subprocess.check_output([openssl, 'version'], text=True).strip(),
    }


def verify_run(run_record, scratch, openssl='openssl'):
    record = json.loads(Path(run_record).read_text())
    if record.get('state') != 'success':
        raise ValueError('successful artifact collector record required')
    artifacts = [row for row in record.get('artifacts', [])
                 if row.get('path', '').endswith('/lineage_ferrari-ota.zip')]
    if len(artifacts) != 1:
        raise ValueError('exactly one collected ferrari OTA required')
    artifact = artifacts[0]
    ota = Path(artifact['collected'])
    if ota.stat().st_size != artifact['bytes'] or sha(ota) != artifact['sha256']:
        raise ValueError('collected OTA changed since successful run')
    scratch = Path(scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ota) as archive:
        result = signer_cert(ota, archive, scratch, openssl)
    result.update({
        'run_record_sha256': sha(run_record),
        'ota_sha256': artifact['sha256'],
        'ota_bytes': artifact['bytes'],
        'signature_pass': result['signer_matches_otacert'],
        'limits': 'SignApk whole-file CMS integrity and signer identity only; no certificate-chain trust, payload application, firmware compatibility or device acceptance',
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-record', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True,
                        help='temporary directory under the repository tmp/ tree')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--openssl', default='openssl')
    args = parser.parse_args()
    result = verify_run(args.run_record, args.scratch, args.openssl)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in
                      ('signature_pass', 'signer_matches_otacert', 'ota_sha256')}))
    return 0 if result['signature_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
