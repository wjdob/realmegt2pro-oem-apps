"""Exercise app-local OEM delegation/fallback without Android privileges."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK required')
class ClockFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        sources = {
            'android/content/pm/PackageManager.java': '''package android.content.pm;
                public class PackageManager { public boolean hasSystemFeature(String n) {
                return "public.feature".equals(n); } }''',
            'android/content/Context.java': '''package android.content; public class Context {
                public android.content.pm.PackageManager getPackageManager() {
                return new android.content.pm.PackageManager(); } }''',
            'android/os/Build.java': '''package android.os; public class Build {
                public static final String MODEL="RMX3301"; }''',
            'android/net/Uri.java': '''package android.net; public class Uri {
                public final String value; public Uri(String value) { this.value=value; } }''',
            'android/media/RingtoneManager.java': '''package android.media;
                public class RingtoneManager { public static final int TYPE_ALARM=4;
                public static android.net.Uri selected=new android.net.Uri("selected");
                public static android.net.Uri getActualDefaultRingtoneUri(android.content.Context c,int type) {
                    if(type!=TYPE_ALARM) throw new AssertionError(); return selected; }
                public static android.net.Uri getDefaultUri(int type) {
                    if(type!=TYPE_ALARM) throw new AssertionError(); return new android.net.Uri("default"); } }''',
            'com/oplus/media/MediaFile.java': '''package com.oplus.media; public class MediaFile {
                public static boolean available;
                public static android.net.Uri getDefaultAlarmUri(android.content.Context c) {
                    if(!available) throw new NoClassDefFoundError(); return new android.net.Uri("oem"); } }''',
            'android/app/OplusNotificationManager.java': '''package android.app;
                public class OplusNotificationManager {
                public static boolean available;
                public OplusNotificationManager() { if(!available) throw new NoSuchMethodError(); }
                public int getAppVisibility(String name,int user) { return 2; }
                public String getOpenid(String name,int user,String type) { return "oem-id"; }
                public void clearOpenid(String name,int user,String type) { } }''',
            'android/view/OplusWindowManager.java': '''package android.view;
                public class OplusWindowManager {
                public static boolean available; public static String lastCommand;
                public OplusWindowManager() { if(!available) throw new NoSuchMethodError(); }
                public void requestKeyguard(String command) { lastCommand=command; } }''',
            'com/oplus/alarmclock/AlarmClockApplication.java': '''package com.oplus.alarmclock;
                public class AlarmClockApplication extends android.content.Context {
                public static boolean absent; public static AlarmClockApplication f() {
                return absent ? null : new AlarmClockApplication(); } }''',
            'com/oplus/content/OplusFeatureConfigManager.java': '''package com.oplus.content;
                public class OplusFeatureConfigManager {
                public static String mode;
                public static OplusFeatureConfigManager getInstance() {
                    if ("absent".equals(mode)) throw new NoClassDefFoundError();
                    if ("denied".equals(mode)) throw new SecurityException();
                    return new OplusFeatureConfigManager(); }
                public boolean hasFeature(String name) {
                    if ("failing".equals(mode)) throw new NoSuchMethodError();
                    return "oem.feature".equals(name); }
                public boolean hasFeatureIPC(String name) { return hasFeature(name); }
                }''',
            'com/oplus/os/OplusBuild.java': '''package com.oplus.os; public class OplusBuild {
                public static boolean fail;
                public static int getOplusOSVERSION() { if(fail) throw new NoSuchMethodError(); return 27; }
                public static String getDeviceName() { if(fail) throw new SecurityException(); return "OEM name"; }
                public static String getDeviceName(android.content.Context context) { return getDeviceName(); }
                public static class VERSION { public static int SDK_VERSION=27, SDK_SUB_VERSION=2;
                public static String RELEASE="15"; } }''',
            'ClockChecks.java': '''import realme.clock.compat.*;
                public class ClockChecks {
                static void check(boolean value) { if(!value) throw new AssertionError(); }
                public static void main(String[] args) {
                String mode=args[0]; com.oplus.content.OplusFeatureConfigManager.mode=mode;
                boolean nativeOk="present".equals(mode);
                FeatureConfig f=FeatureConfig.getInstance();
                check(f==FeatureConfig.getInstance());
                check(f.hasFeature("oem.feature")==nativeOk);
                check(f.hasFeatureIPC("oem.feature")==nativeOk);
                check(f.hasFeature("public.feature")==!nativeOk);
                check(!f.hasFeature("missing.feature") && !f.hasFeature(null) && !f.hasFeature(""));
                if(!nativeOk) { com.oplus.alarmclock.AlarmClockApplication.absent=true;
                    check(!f.hasFeature("public.feature")); }
                com.oplus.os.OplusBuild.fail=!nativeOk;
                check(OemBuild.getOplusOSVERSION()==(nativeOk?27:0));
                check(OemBuild.getDeviceName().equals(nativeOk?"OEM name":"RMX3301"));
                check(OemBuild.getDeviceName(new android.content.Context()).equals(nativeOk?"OEM name":"RMX3301"));
                check(OemBuild.VERSION.SDK_VERSION==27 && OemBuild.VERSION.SDK_SUB_VERSION==2);
                check(OemBuild.VERSION.RELEASE.equals("15"));
                com.oplus.media.MediaFile.available=nativeOk;
                android.content.Context context=new android.content.Context();
                check(AlarmMedia.getDefaultAlarmUri(context).value.equals(nativeOk?"oem":"selected"));
                if(!nativeOk) { android.media.RingtoneManager.selected=null;
                    check(AlarmMedia.getDefaultAlarmUri(context).value.equals("default")); }
                android.app.OplusNotificationManager.available=nativeOk;
                OemNotifications notifications=new OemNotifications();
                check(notifications.getAppVisibility("self",0)==(nativeOk?2:0));
                check(nativeOk?"oem-id".equals(notifications.getOpenid("self",0,"id")):
                    notifications.getOpenid("self",0,"id")==null);
                notifications.clearOpenid("self",0,"id");
                android.view.OplusWindowManager.available=nativeOk;
                OptionalKeyguard.request("self-test");
                check(nativeOk?"self-test".equals(android.view.OplusWindowManager.lastCommand):
                    android.view.OplusWindowManager.lastCommand==null);
                check(ClockInsetPolicy.bottomPadding(0,0,2412,2412,72)==72);
                check(ClockInsetPolicy.bottomPadding(0,72,2340,2412,72)==72);
                check(ClockInsetPolicy.bottomPadding(0,0,2340,2412,72)==0);
                check(ClockInsetPolicy.bottomPadding(8,80,2340,2412,72)==80);
                check(ClockInsetPolicy.bottomPadding(0,72,2340,2412,0)==0);
                check(ClockInsetPolicy.bottomPadding(0,0,2500,2412,72)==72);
                } }''',
        }
        files = []
        for name, body in sources.items():
            path = cls.work/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            files.append(path)
        files.extend((ROOT/'tools/oem_clock_compat').glob('FeatureConfig.java'))
        files.extend((ROOT/'tools/oem_clock_compat').glob('OemBuild.java'))
        files.extend((ROOT/'tools/oem_clock_compat').glob('AlarmMedia.java'))
        files.extend((ROOT/'tools/oem_clock_compat').glob('OemNotifications.java'))
        files.extend((ROOT/'tools/oem_clock_compat').glob('OptionalKeyguard.java'))
        files.extend((ROOT/'tools/oem_clock_compat').glob('ClockInsetPolicy.java'))
        subprocess.run(['javac', '--release', '8', '-d', str(cls.work), *map(str, files)],
                       check=True, capture_output=True, timeout=60)

    def check_mode(self, mode):
        subprocess.run(['java', '-cp', str(self.work), 'ClockChecks', mode],
                       check=True, capture_output=True, timeout=20)

    def test_accessible_oem_values_preserved(self): self.check_mode('present')
    def test_missing_oem_feature_uses_public_fallback(self): self.check_mode('absent')
    def test_denied_oem_feature_uses_public_fallback(self): self.check_mode('denied')
    def test_failing_oem_method_uses_public_fallback(self): self.check_mode('failing')
