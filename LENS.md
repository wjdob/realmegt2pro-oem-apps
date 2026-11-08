# Camera Lens button

The installed Google Lens app works directly. Camera's native old Google link
does nothing on the tested Android 17 installation.

The earlier adapter looked for `google_lens_id`. That resource exists in Camera's
APK, but the real button has no resource ID. Its native menu key is
`key_show_googlelens_icon`. The earlier tests used the unused resource ID and
missed this binding error.

Alpha4 binds that exact menu option in the pinned Camera APK. Live logs confirm
the binding and modern URL request, but Google immediately returns to Camera.
Google logs: "Caller package cannot be empty. LensExportedActivity must be started
for result." The helper used `startActivity`, while the OEM path used
`startActivityForResult`. The new candidate restores that result launch with the
original request code 0, allowing Android to supply the calling package. It
keeps the native touch listener, click-state reset and capture/pause checks. It
prefers Google's `google://lens` link and retains the older link as a fallback.
Google remains optional. A locked or background Camera cannot launch Lens.

Relative to alpha4, each app package changes only its shared SDK, receipt and OTA
retention hook. Both APKs, signing identities, native libraries and permissions
stay the same. The accepted HEVC, HEIC-sharing and thumbnail fixes are retained.
The base ROM and boot images stay unchanged.

## Build

Use the private inputs and existing signing keys identified by the configurations.

```sh
python tools/oem_camera_gallery.py --base-sdk LENS16_SDK \
  --android-tree ANDROID_TREE --output LENS19_SDK --scratch SCRATCH \
  --contract configs/oem-camera-lens19-23.2.json
python tools/oem_lens_result_bundle.py --config configs/oem-addons-lens19-24.0.json \
  --source-dir PREVIOUS_RELEASE_ZIPS --sdk LENS19_SDK \
  --gallery-apk ALPHA4_PHOTOS_APK --android-tree ANDROID_TREE \
  --signing-dir RECOVERY_SIGNING_DIR --out OUTPUT --scratch SCRATCH
```

The builder checks Camera's actual native menu ABI before packaging. It accepts
four exact API37 predecessors: alpha1, alpha2, alpha3 and alpha4. The Camera ZIP also
updates a recognised Photos installation. Camera-only users keep Photos absent;
Photos-only users use the direct Photos ZIP. Unknown or mixed files stop the
update. The existing child checks and recovery return paths remain in use.
The coordinated sequence is not power-loss atomic. If Photos fails after Camera
updates, keep recovery open and install matching Photos before rebooting.

The SDK recipe's `23.2` suffix identifies its shared source lineage. This package
targets Android 17/API37. Earlier API36 recipes remain unchanged.

The new API37 installer also stages small files at 4096 bytes while applying
metadata, then trims them to their original size before atomic replacement. A
Photos XML failed durable verification during alpha4 installation; the guarded
repair using this sequence passed full readback. The underlying filesystem cause
remains unproven. The new installer retains durable verification and needs its
own installation test. Older package recipes retain their original bytes.

## Validation

Host tests cover the no-ID keyed control, native click refusal, stale views,
pause/lock/background states, listener restoration and missing Google handlers.
Integration tests run the actual Gallery/thumbnail bridge and Lens binding
together. The SDK reproduces byte-for-byte; its other six DEX files are unchanged.
These checks do not establish that tapping the real button works on the phone.

After installation readback, check Lens from normal Photo mode, return to Camera,
switch modes and tap again. Also check an ordinary capture, newest-photo handoff,
HEVC persistence and Photos' compatible HEIC export. Keep a locked-screen check
and app-data comparison in the same session. Installation and this UAT are pending.
