"""Build a version-bound optional thumbnail adapter without editing Camera's APK."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

from oem_camera_window import abi, source_files, ADAPTER_TYPES
from oem_sdk import definitions, sha, write_jar

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'configs/oem-camera-gallery-23.2.json'
GALLERY = ROOT / 'tools/oem_camera_compat/realme/camera/compat/GalleryShortcut.java'
THUMBNAIL = ROOT / 'tools/oem_camera_compat/realme/camera/compat/CaptureThumbnail.java'
GALLERY_TYPES = {'Lrealme/camera/compat/GalleryShortcut' + part + ';'
                 for part in ('', '$Cancel', '$Lookup')}
GALLERY_TYPES |= {'Lrealme/camera/compat/CaptureThumbnail' + part + ';'
                  for part in ('', '$Cancel', '$Changes', '$Load', '$Deliver')}
FORMAT_DIRECTORY = 'tools/oem_camera_compat/formats17/realme/camera/compat'
FORMAT_TYPE = 'Lrealme/camera/compat/EncoderMenuCompatibility;'


def sources(contract=None):
    directory = (contract or {}).get('adapter_source_directory')
    if directory is None:
        return [*source_files(), GALLERY, THUMBNAIL]
    # Retain historical adapter bytes/recipes while new fixes are independently
    # hash-bound. Do not allow an arbitrary path from a contract into javac.
    if directory not in ('tools/oem_camera_compat/gallery13/realme/camera/compat',
                          'tools/oem_camera_compat/lens16/realme/camera/compat',
                          FORMAT_DIRECTORY):
        raise ValueError('unreviewed gallery adapter source directory')
    root = ROOT / directory
    selected = [root/'GalleryShortcut.java', root/'CaptureThumbnail.java']
    if directory == FORMAT_DIRECTORY:
        selected.append(ROOT/'tools/oem_camera_compat/EncoderMenuCompatibility.java')
    if any(p.is_symlink() or not p.is_file() for p in selected):
        raise ValueError('missing or symlinked gallery adapter source')
    return [*source_files(), *selected]


def compile_parts(android, work, contract=None):
    work.mkdir(parents=True, exist_ok=True)
    for name in ('classes', 'dex', 'patched/ek'):
        (work / name).mkdir(parents=True)
    original = source_files()[0].read_text()
    markers = [
        ('        private final WeakReference<Activity> activity;',
         '        private final WeakReference<Activity> activity;\n'
         '        private final realme.camera.compat.GalleryShortcut gallery;'),
        ('            this.activity = new WeakReference<>(activity);',
         '            this.activity = new WeakReference<>(activity);\n'
         '            this.gallery = new realme.camera.compat.GalleryShortcut(activity);'),
        ('            boolean previous = owner.a;',
         '            gallery.bind();\n            boolean previous = owner.a;'),
        ('        private void detach() {',
         '        private void detach() {\n            gallery.close();')]
    for before, after in markers:
        if original.count(before) != 1:
            raise ValueError('unreviewed window thumbnail hook location')
        original = original.replace(before, after)
    patched = work / 'patched/ek/v0.java'
    patched.write_text(original)
    java = android / 'prebuilts/jdk/jdk21/linux-x86/bin'
    sdk = android / 'prebuilts/sdk/36/public/android.jar'
    adapters = sources(contract)[len(source_files()):]
    subprocess.run([str(java/'javac'), '--release', '17', '-cp', str(sdk),
                    '-d', str(work/'classes'), str(patched),
                    *map(str, source_files()[1:]), *map(str, adapters)],
                   check=True, capture_output=True)
    names = ADAPTER_TYPES | GALLERY_TYPES
    if (contract or {}).get('adapter_source_directory') == FORMAT_DIRECTORY:
        names = names | {FORMAT_TYPE}
    jar = work / 'adapter.jar'
    with zipfile.ZipFile(jar, 'w') as z:
        for name in sorted(names):
            member = name[1:-1] + '.class'
            z.writestr(member, (work/'classes'/member).read_bytes())
    subprocess.run([str(java/'java'), '-Xmx256m', '-cp', str(android/'prebuilts/r8/r8.jar'),
                    'com.android.tools.r8.D8', '--lib', str(sdk), '--min-api', '36',
                    '--output', str(work/'dex'), str(jar)], check=True, capture_output=True)
    dex = (work/'dex/classes.dex').read_bytes()
    if {n for n, _ in definitions(dex)} != names:
        raise ValueError('unexpected thumbnail adapter classes or compile stubs')
    return dex


def build(base, android, output, scratch, contract_path=CONTRACT):
    contract = json.loads(contract_path.read_text())
    actual = {str(p.relative_to(ROOT)): sha(p.read_text().encode()) for p in sources(contract)}
    if actual != contract['sources_sha256'] or sha(base.read_bytes()) != contract['base_sdk_sha256']:
        raise ValueError('unreviewed gallery SDK/source input')
    if output.exists():
        raise ValueError('refusing to overwrite prepared gallery SDK')
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='camera-gallery-', dir=scratch) as directory:
        dex = compile_parts(android, Path(directory), contract)
        if sha(dex) != contract['adapter_dex_sha256']:
            raise ValueError('canonical gallery adapter DEX differs')
        with zipfile.ZipFile(base) as z:
            members = {n: z.read(n) for n in z.namelist()}
        if set(members) != {'classes.dex', *('classes'+str(i)+'.dex' for i in range(2, 8))}:
            raise ValueError('unexpected gallery base SDK members')
        base_types = set(contract.get('base_adapter_types', ADAPTER_TYPES))
        if not ADAPTER_TYPES <= base_types <= ADAPTER_TYPES | GALLERY_TYPES:
            raise ValueError('unreviewed original adapter type contract')
        if {n for n, _ in definitions(members['classes4.dex'])} != base_types:
            raise ValueError('unexpected original window adapter class scope')
        if any({n for n, _ in definitions(data)} & GALLERY_TYPES
               for name, data in members.items() if name != 'classes4.dex'):
            raise ValueError('gallery adapter conflicts with base SDK')
        if abi(dex, {'Lek/v0;'}) != abi(members['classes4.dex'], {'Lek/v0;'}):
            raise ValueError('window public helper ABI changed')
        unchanged = {name: sha(data) for name, data in members.items()
                     if name != 'classes4.dex'}
        members['classes4.dex'] = dex
        output.parent.mkdir(parents=True, exist_ok=True)
        write_jar(output, members)
        if sha(output.read_bytes()) != contract['output_sdk_sha256']:
            raise ValueError('canonical gallery SDK differs')
    report = dict(base_sdk_sha256=contract['base_sdk_sha256'], sources_sha256=actual,
                  adapter_dex_sha256=sha(dex), output_sha256=sha(output.read_bytes()),
                  unchanged_dex_sha256=unchanged,
                  classes=sorted({n for n,_ in definitions(dex)}), scope=contract['scope'],
                  limits=contract['limits'])
    output.with_suffix('.provenance.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-sdk', 'android-tree', 'output', 'scratch'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--contract', type=Path, default=CONTRACT)
    args = parser.parse_args()
    print(json.dumps(build(args.base_sdk, args.android_tree, args.output, args.scratch,
                           args.contract), indent=2))
