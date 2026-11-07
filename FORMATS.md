# Camera and Photos formats candidate

This app-only update targets Native25, LineageOS 24.0 / Android 17.
Alpha3 passed installation, retained-settings checks and format tests on
November 7, 2026. HEVC recording at 1080p/4K and 30/60 fps, the AVC control,
compatible single/multiple HEIC shares, image appearance and the privacy export
passed. The existing Lens button still fails. [Its follow-up](LENS.md) has separate
source and package recipes. Public downloads have not changed.

The owner confirms that Messenger sends an 8-bit HEIC but rejects a 10-bit HEIC.
JPEG sends normally. For direct sending through Messenger's picker, capture with
10-bit colour off. This candidate adds Photos' optional compatible-JPEG export
for sharing a converted copy while keeping the original HEIC.

Camera's video setting uses the highest menu resolution to decide HEVC support.
Native25 advertises AVC and HEVC encoding at 4K and 1080p, with neither advertising
8K. The optional SDK removes a resolution only when both encoders reject it,
then lets Camera recompute support. This is a software capability measurement;
it does not establish the hardware's limits.

Camera keeps its original APK and OEM signature. Photos keeps its existing
dedicated signing key, data and permission choices. Its fallback uses Android's
public decoder and JPEG encoder when the OEM stream converter is absent.
The existing temporary-file, metadata, privacy and sharing paths remain in use.
Conversion remains opt-in. The base ROM, Google apps and global codecs stay as
installed.

## Build

Supply the private inputs and existing signing identities listed in the configs.

```sh
python tools/oem_gallery_heic.py --apk PREVIOUS_PHOTOS_APK \
  --android ANDROID_TREE --output UNSIGNED_PHOTOS_APK --work NEW_WORK_DIR
```

Align to 16 KiB and sign the prepared Photos APK with its existing Gallery key.
Use apksigner's `--alignment-preserved true`, then verify the signature and
alignment. The final APK must match the hash in the formats configuration.

```sh
python tools/oem_camera_gallery.py --base-sdk LENS16_SDK \
  --android-tree ANDROID_TREE --output FORMATS_SDK --scratch SCRATCH \
  --contract configs/oem-camera-formats17-23.2.json
python tools/oem_formats_bundle.py --config configs/oem-addons-formats17-24.0.json \
  --source-dir PREVIOUS_RELEASE_ZIPS --sdk FORMATS_SDK \
  --gallery-apk SIGNED_PHOTOS_APK --android-tree ANDROID_TREE \
  --signing-dir RECOVERY_SIGNING_DIR --out OUTPUT --scratch SCRATCH
```

The SDK recipe's `23.2` suffix identifies its shared source lineage. The package
configuration and recovery guards target only Android 17 / API37 for this trial.

The Camera install ZIP recognizes both earlier API37 releases. It also updates
Photos when an exact supported installation is present. Camera-only users keep
Photos absent. Photos-only users use the direct Photos ZIP. Users with both apps
start with the coordinated Camera ZIP. Unknown or mixed files stop installation.
Each child has rollback checks; the full sequence is not power-loss atomic.
If Photos installation fails after Camera updates, keep recovery open and install
the matching Photos ZIP before rebooting. Existing media and app data are retained.

Recognition of a complete installed version uses a small disk-space allowance.
Fresh, incomplete or changed files retain the full installation checks. Recovery
RAM staging keeps its full budget in both cases.

## Combined test

Check these in one session after independent installation readback:

- HEVC switch persistence, saved HEVC/AVC codec, audio and playback at 1080p/4K.
- 8-bit and 10-bit HEIC capture; Photos conversion choice; single and multiple
  compatible JPEG shares to Messenger; orientation, colour, privacy options and
  cancellation. Confirm original files remain unchanged.
- Camera photo/video, filters, Portrait, flash, thumbnail handoff and Lens.
- Photos Markup, Remover, Save copy, Replace original, album moves and Share.
- Retained app settings, media access, PIN/fingerprint and Google session.

Full-resolution conversion has input and memory limits. Oversized or unreadable
inputs must fail without changing the original. A successful helper conversion
does not establish successful Gallery export or Messenger sending.
