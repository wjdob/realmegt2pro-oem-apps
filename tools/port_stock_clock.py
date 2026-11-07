"""Build a private Clock compatibility APK; no signing or device operations.

Preserve the manifest/resources/version and adapt five OEM framework types to
app-local delegates with public-API fallbacks. Reverse-rewrite and compare every
original DEX class before accepting the output. Alarm reliability is a separate gate.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import copy
import hashlib
import json
import re
import subprocess
import zipfile

from bootstrap_gallery_dex import verified_paths
from oem_sdk import definitions
import oem_gallery_dex

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = 'cf7c12426d464199e15b55cd2645bcf6e90d8bde75e3c0f58bc9a1b63ec23b20'


def optional_keyguard(text):
    replacements = {
        '.method public constructor <init>()V': '''.method public constructor <init>()V
    .locals 0
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V
    return-void
.end method''',
        '.method public a(Ljava/lang/String;)V': '''.method public a(Ljava/lang/String;)V
    .locals 0
    invoke-static {p1}, Lrealme/clock/compat/OptionalKeyguard;->request(Ljava/lang/String;)V
    return-void
.end method''',
    }
    for marker, replacement in replacements.items():
        if text.count(marker) != 1:
            raise ValueError('Unreviewed optional keyguard method')
        start = text.index(marker)
        end = text.index('.end method', start)+len('.end method')
        original = text[start:end]
        if 'Landroid/view/OplusWindowManager;' not in original:
            raise ValueError('OEM keyguard call missing')
        text = text[:start]+replacement+text[end:]
    return text


def clock_insets(text):
    marker = '.method public onApplyWindowInsets(Landroid/view/View;Landroid/view/WindowInsets;)Landroid/view/WindowInsets;'
    if text.count(marker) != 1 or 'Lrealme/clock/compat/ClockInsets;' in text:
        raise ValueError('Unreviewed Clock inset listener')
    start = text.index(marker)
    end = text.index('.end method', start)
    method = text[start:end]
    old = '    return-object p2'
    if method.count(old) != 1:
        raise ValueError('Unreviewed inset return')
    method = method.replace(old, '    invoke-static {p0, p2}, Lrealme/clock/compat/ClockInsets;->apply(Landroid/app/Activity;Landroid/view/WindowInsets;)V\n\n'+old)
    return text[:start]+method+text[end:]


def register_insets(text):
    marker = '    invoke-super {p0}, Landroid/app/Application;->onCreate()V'
    if text.count(marker) != 1 or 'Lrealme/clock/compat/ClockInsets;' in text:
        raise ValueError('Unreviewed application lifecycle')
    return text.replace(marker, marker+'\n\n    invoke-static {p0}, Lrealme/clock/compat/ClockInsets;->register(Landroid/app/Application;)V')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(apk, android, out, dependency_cache):
    if out.exists() or sha(apk) != SOURCE_SHA:
        raise ValueError('New output and exact reviewed stock APK required')
    out.mkdir(parents=True)
    java = android/'prebuilts/jdk/jdk21/linux-x86/bin'
    smali = android/'prebuilts/extract-tools/common/smali/baksmali.jar'
    library = ':'.join(map(str, [*verified_paths(dependency_cache), smali]))
    source = ROOT/'tools/oem_clock_compat'
    sequence = 0

    def run(args):
        nonlocal sequence
        sequence += 1
        result = subprocess.run(list(map(str, args)), capture_output=True, timeout=600)
        (out/f'command-{sequence}.txt').write_bytes(result.stdout+result.stderr)
        result.check_returncode()
        return result.stdout

    host = out/'host'
    host.mkdir()
    run([java/'javac', '--release', '17', '-cp', library, '-d', host,
         source/'ClockDexCompat.java',
         ROOT/'tools/oem_gallery_compat/GalleryDexSemanticCheck.java'])
    hostcp = str(host)+':'+library
    # Compile only a signature stub; it is excluded from the packaged helper DEX.
    stub = out/'stubs/com/oplus/alarmclock/AlarmClockApplication.java'
    stub.parent.mkdir(parents=True)
    stub.write_text('package com.oplus.alarmclock; public class AlarmClockApplication '
                    'extends android.app.Application { public static AlarmClockApplication f() '
                    '{ throw new AssertionError("compile-only stub"); } }\n')
    classes = out/'classes'
    classes.mkdir()
    sdk = android/'prebuilts/sdk/36/public/android.jar'
    run([java/'javac', '--release', '8', '-cp', sdk, '-d', classes,
         stub, source/'FeatureConfig.java', source/'OemBuild.java',
         source/'AlarmMedia.java', source/'OemNotifications.java', source/'OptionalKeyguard.java',
         source/'ClockInsets.java', source/'ClockInsetPolicy.java'])
    dexdir = out/'helpers'
    dexdir.mkdir()
    helpers = sorted((classes/'realme/clock/compat').glob('*.class'))
    assert len(helpers) == 10
    run([java/'java', '-Xmx1g', '-cp', android/'prebuilts/r8/r8.jar',
         'com.android.tools.r8.D8', '--min-api', '28', '--lib', sdk,
         '--classpath', classes, '--output', dexdir, *helpers])
    helperdex = dexdir/'classes.dex'
    helper_names = {n for n, _ in definitions(helperdex.read_bytes())}
    assert helper_names == {'Lrealme/clock/compat/FeatureConfig;',
                            'Lrealme/clock/compat/OemBuild;', 'Lrealme/clock/compat/OemBuild$VERSION;',
                            'Lrealme/clock/compat/AlarmMedia;', 'Lrealme/clock/compat/OemNotifications;',
                            'Lrealme/clock/compat/OptionalKeyguard;', 'Lrealme/clock/compat/ClockInsets;',
                            'Lrealme/clock/compat/ClockInsets$State;', 'Lrealme/clock/compat/ClockInsets$1;',
                            'Lrealme/clock/compat/ClockInsetPolicy;'}
    reports = []
    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate APK entries')
        dexnames = sorted(n for n in names if n.startswith('classes') and n.endswith('.dex'))
        original_classes = set()
        replacements = {}
        for index, name in enumerate(dexnames):
            work = out/f'dex-{index}'
            work.mkdir()
            before, after, reverse = (work/n for n in ('before.dex', 'after.dex', 'reverse.dex'))
            before.write_bytes(z.read(name))
            classes_before = {n for n, _ in definitions(before.read_bytes())}
            keyguard_report = None
            if 'Lo3/a;' in classes_before:
                original_keyguard_dex = work/'original-before-keyguard.dex'
                original_keyguard_dex.write_bytes(before.read_bytes())
                directory = work/'keyguard-smali'
                run([java/'java', '-Xmx512m', '-jar', smali, 'disassemble', '--api', '31',
                     '--use-locals', '--classes', 'Lo3/a;', '--output', directory, before])
                text = directory/'o3/a.smali'
                text.write_text(optional_keyguard(text.read_text()))
                adapter = work/'keyguard-adapter.dex'
                run([java/'java', '-Xmx512m', '-jar', android/'prebuilts/extract-tools/common/smali/smali.jar',
                     'assemble', '--api', '31', '--output', adapter, directory])
                oem_gallery_dex.verified_paths = lambda: verified_paths(dependency_cache)
                changed, keyguard_report = oem_gallery_dex.merge_class(
                    before.read_bytes(), adapter, 'Lo3/a;', android, work/'keyguard-merge')
                before.write_bytes(changed)
            lifecycle_report = None
            if 'Lcom/oplus/alarmclock/AlarmClockApplication;' in classes_before:
                directory = work/'lifecycle-smali'
                run([java/'java', '-Xmx512m', '-jar', smali, 'disassemble', '--api', '31',
                     '--use-locals', '--classes', 'Lcom/oplus/alarmclock/AlarmClockApplication;', '--output', directory, before])
                text = directory/'com/oplus/alarmclock/AlarmClockApplication.smali'
                text.write_text(register_insets(text.read_text()))
                adapter = work/'lifecycle-adapter.dex'
                run([java/'java', '-Xmx512m', '-jar', android/'prebuilts/extract-tools/common/smali/smali.jar',
                     'assemble', '--api', '31', '--output', adapter, directory])
                oem_gallery_dex.verified_paths = lambda: verified_paths(dependency_cache)
                changed, lifecycle_report = oem_gallery_dex.merge_class(before.read_bytes(), adapter,
                    'Lcom/oplus/alarmclock/AlarmClockApplication;', android, work/'lifecycle-merge')
                before.write_bytes(changed)
            insets_report = None
            if 'Lcom/oplus/alarmclock/AlarmClock;' in classes_before:
                directory = work/'insets-smali'
                run([java/'java', '-Xmx512m', '-jar', smali, 'disassemble', '--api', '31',
                     '--use-locals', '--classes', 'Lcom/oplus/alarmclock/AlarmClock;', '--output', directory, before])
                text = directory/'com/oplus/alarmclock/AlarmClock.smali'
                text.write_text(clock_insets(text.read_text()))
                adapter = work/'insets-adapter.dex'
                run([java/'java', '-Xmx512m', '-jar', android/'prebuilts/extract-tools/common/smali/smali.jar',
                     'assemble', '--api', '31', '--output', adapter, directory])
                oem_gallery_dex.verified_paths = lambda: verified_paths(dependency_cache)
                changed, insets_report = oem_gallery_dex.merge_class(before.read_bytes(), adapter,
                    'Lcom/oplus/alarmclock/AlarmClock;', android, work/'insets-merge')
                before.write_bytes(changed)
            if original_classes & classes_before or helper_names & classes_before:
                raise ValueError('Class collision')
            original_classes |= classes_before
            run([java/'java', '-Xmx1g', '-cp', hostcp, 'ClockDexCompat', 'forward', before, after])
            run([java/'java', '-Xmx1g', '-cp', hostcp, 'ClockDexCompat', 'reverse', after, reverse])
            # Use baksmali's own formatter, without dexlib2 utility shadowing.
            compared = json.loads(run([java/'java', '-Xmx1g', '-cp', str(host)+':'+str(smali),
                                      'GalleryDexSemanticCheck', before, reverse, work/'diff',
                                      '--normalize-defaults']))
            assert compared['matching'] and compared['classes'] == len(classes_before)
            reports.append({'member': name, 'before_sha256': sha(before),
                            'after_sha256': sha(after), 'reverse_comparison': compared,
                            'optional_keyguard_merge': keyguard_report, 'clock_insets_merge': insets_report,
                            'application_lifecycle_merge': lifecycle_report})
            replacements[name] = after.read_bytes()
        helper_member = f'classes{len(dexnames)+1}.dex'
        assert helper_member not in names
        unsigned = out/'realme-clock-15.10.13-alpha5-unsigned.apk'
        with zipfile.ZipFile(unsigned, 'x') as dest:
            for info in z.infolist():
                upper = info.filename.upper()
                if upper.startswith('META-INF/') and (upper.endswith(('.SF', '.RSA', '.DSA', '.EC'))
                                                       or upper == 'META-INF/MANIFEST.MF'):
                    continue
                dest.writestr(copy.copy(info), replacements.get(info.filename, z.read(info.filename)))
            dest.writestr(helper_member, helperdex.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(unsigned) as result:
            for name in names:
                if name not in replacements and name in result.namelist():
                    assert result.read(name) == z.read(name), name
    report = {'utc': datetime.now(timezone.utc).isoformat(), 'source_apk_sha256': SOURCE_SHA,
              'unsigned_apk': unsigned.name, 'unsigned_apk_sha256': sha(unsigned),
              'preserved_original_classes': len(original_classes), 'dex': reports,
              'helper_dex_member': helper_member, 'helper_classes': sorted(helper_names),
              'manifest_resources_native_unchanged': True,
              'sources': {p.name: sha(p) for p in source.glob('*.java')},
              'limits': 'Unsigned candidate. No installation, grants, framework changes or alarm acceptance.'}
    (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('apk', 'android', 'out', 'dependency-cache'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    report = build(args.apk, args.android, args.out, args.dependency_cache)
    print(json.dumps({k: report[k] for k in ('unsigned_apk_sha256', 'preserved_original_classes')}))
