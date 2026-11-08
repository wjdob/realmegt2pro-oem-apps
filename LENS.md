# Camera Lens button

The alpha6 candidate opens the installed **Google Lens** app through its standard
Android launcher. Install Google Lens to use this button. Other Camera modes work
without it. The Google app supplies Lens' own dependencies.

Camera keeps its original APK and OEM signature. The optional SDK binds the exact
native menu key and preserves its click gate, touch handling and lifecycle checks.
The base ROM, Google apps, permissions and system properties stay unchanged.

## Why the launcher is used

The native OEM entry requires caller context and Google's OEM eligibility checks.
Restoring Camera's result-launch protocol reached that eligibility check, but
Google rejected the caller. Its code checks OEM Lens features and package
properties, which are absent on the tested LineageOS installation. Runtime Google
caller lists also participate and were not inspected.

The installed Lens app prepares its own Google initialization. A temporary app
with no permissions launched it from an ordinary app UID; a working live view was
confirmed. The probe was removed. The candidate uses the same launcher call.
The actual Camera button still needs its own device test.

## Build

Use the private inputs and existing signing keys listed in the configurations.

```sh
python tools/oem_camera_gallery.py --base-sdk LENS16_SDK \
  --android-tree ANDROID_TREE --output LENS20_SDK --scratch SCRATCH \
  --contract configs/oem-camera-lens20-23.2.json
python tools/oem_lens_launch_bundle.py --config configs/oem-addons-lens20-24.0.json \
  --source-dir PREVIOUS_RELEASE_ZIPS --sdk LENS20_SDK \
  --gallery-apk ALPHA5_PHOTOS_APK --android-tree ANDROID_TREE \
  --signing-dir RECOVERY_SIGNING_DIR --out OUTPUT --scratch SCRATCH
```

This package targets Android 17/API37. The SDK recipe's `23.2` suffix identifies
its shared source lineage. API36 recipes remain unchanged.

The coordinator supports public alpha1/alpha2 and the current private alpha5.
It updates recognised Photos and preserves Camera-only installs. Photos-only
users use the direct Photos ZIP. Unknown or mixed files stop the update.
Each child retains rollback checks; the sequence is not power-loss atomic.
If Photos fails after Camera updates, keep recovery open and install matching
Photos before rebooting. Earlier source recipes remain available.

Small files use the extent-sized staging that passed the alpha5 installation.
Durable verification remains mandatory. The earlier filesystem failure's cause
remains unproven.

## Test

After independent readback, test Lens, return to Camera, switch modes and try
again. Check an ordinary capture, newest-photo handoff and retained app settings.
Keep new sign-in or upload consent prompts unanswered during diagnosis.
Host tests and a successful probe do not establish actual Camera-button success.
