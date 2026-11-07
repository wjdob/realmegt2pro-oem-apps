"""Fetch exact host-only DEX writer dependencies; no device/source-tree changes."""
from pathlib import Path
import hashlib
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'.local/tools/gallery-dexlib2-2.5.2'
DEPENDENCIES=(
 ('dexlib2-2.5.2.jar','org/smali/dexlib2/2.5.2','5a5c8982d8bd7d6e3bb1a0713049e3c78b719ec32b20f6b619885cec30a0dd61'),
 ('guava-27.1-android.jar','com/google/guava/guava/27.1-android','686404f2d1d4d221911f96bd627ff60dac2226a5dfa6fb8ba517073eb97ec0ef'),
 ('jsr305-3.0.2.jar','com/google/code/findbugs/jsr305/3.0.2','766ad2a0783f2687962c8ad74ceecc38a28b9f72a2d085ee438b7813e928d0c7'),
)


def verified_paths(cache=CACHE):
    result=[]
    for filename,_,expected in DEPENDENCIES:
        p=cache/filename
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            raise ValueError('Missing/changed host DEX dependency; run tools/bootstrap_gallery_dex.py: '+filename)
        result.append(p)
    return result


def fetch(cache=CACHE):
    cache.mkdir(parents=True,exist_ok=True)
    for filename,directory,expected in DEPENDENCIES:
        dest=cache/filename
        if dest.exists():
            if not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest()!=expected:
                raise ValueError('Refusing to overwrite changed dependency: '+str(dest))
            continue
        url='https://repo.maven.apache.org/maven2/'+directory+'/'+filename
        with urllib.request.urlopen(url,timeout=60) as response:data=response.read(4*1024**2+1)
        if len(data)>4*1024**2 or hashlib.sha256(data).hexdigest()!=expected:raise ValueError('Dependency hash/size differs: '+filename)
        with dest.open('xb') as f:f.write(data)
    return verified_paths(cache)


if __name__=='__main__':
    for p in fetch():print(p.name)
