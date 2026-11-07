"""Stage the pinned, unchanged OEM Clock APK for later optional evaluation.

No device operations, permission grants, system placement or app re-signing.
Staging does not qualify startup or alarm reliability on either Android version.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,re,subprocess

ROOT=Path(__file__).resolve().parents[1]

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def stage(apk,android,out):
    config_path=ROOT/'configs/stock-clock-port.json';config=json.loads(config_path.read_text())
    if out.exists():raise ValueError('new private output directory required')
    if sha(apk)!=config['apk_sha256']:raise ValueError('unreviewed Clock APK')
    java=android/'prebuilts/jdk/jdk21/linux-x86/bin/java'
    signer=android/'prebuilts/sdk/tools/linux/lib/apksigner.jar'
    result=subprocess.run([str(java),'-jar',str(signer),'verify','--verbose','--print-certs',str(apk)],
                          capture_output=True,text=True,timeout=120)
    if result.returncode:raise ValueError('Clock APK signature verification failed')
    certificates=re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-f]{64})$',result.stdout,re.M)
    if certificates!=[config['certificate_sha256']]:raise ValueError('Clock OEM signer differs')
    data=apk.read_bytes()
    if hashlib.sha256(data).hexdigest()!=config['apk_sha256']:raise ValueError('Clock APK changed during verification')
    out.mkdir(parents=True,exist_ok=False)
    destination=out/'realme-clock-15.10.13-stock-evaluation.apk';destination.write_bytes(data)
    if sha(destination)!=config['apk_sha256']:raise ValueError('staged APK hash differs')
    (out/'SHA256SUMS').write_text(config['apk_sha256']+'  '+destination.name+'\n')
    (out/'signature.txt').write_text(result.stdout)
    report={'created_utc':datetime.now(timezone.utc).isoformat(),'config':config,
            'config_sha256':sha(config_path),'builder_sha256':sha(Path(__file__)),
            'apksigner_sha256':sha(signer),'java_sha256':sha(java),
            'artifact':destination.name,'bytes':destination.stat().st_size,
            'sha256':sha(destination),'apk_unchanged':True,'device_install_performed':False,
            'limits':'Private stock-signed evaluation artifact only. This staging operation performs no installation or permission change. Runtime and alarm acceptance require separate evidence.'}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apk',type=Path,required=True);p.add_argument('--android-tree',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    report=stage(a.apk,a.android_tree,a.out);print(json.dumps({k:report[k] for k in ('artifact','sha256','bytes','device_install_performed')}))

if __name__=='__main__':main()
