# Optional Realme apps

Camera and Photos/editor compatibility tools and recovery-package builders for
**RMX3301 on LineageOS 24.0 / Android 17**.
Clock alpha5 installs as a normal APK and has basic Android 17 test coverage.

[Downloads and installation](https://github.com/wjdob/lineageos-realmegt2pro/tree/lineage-24.0) |
[Known issues](https://github.com/wjdob/lineageos-realmegt2pro/blob/lineage-24.0/RELEASE_NOTES.md) |
[Report a problem](https://github.com/wjdob/lineageos-realmegt2pro/issues/new/choose)

These apps are optional. The base ROM and Google apps are installed separately.

## Developer setup

Use Linux and Python 3.12:

```sh
python -m pip install -r requirements-dev.txt
python tools/validate_source.py
python -m unittest discover -s tools/tests -v
```

Some tests require additional Android/JDK tools and report a skip when those are
missing. Phone-test results are listed in the release notes.

## Package inputs and tools

Builds need matching OEM APKs, native libraries, model files, Android build tools
and signing keys. Keep these files and generated packages outside Git.
The configurations list expected input paths and SHA-256 hashes.

| Tool | Purpose |
| --- | --- |
| `tools/oem_addons.py` | Build signed Camera/Photos install, check and remove ZIPs |
| `tools/prepare_oem37_sources.py` | Prepare the Android 17 Camera/Photos input files |
| `tools/port_stock_clock.py` | Create the unsigned Clock compatibility APK |
| `tools/validate_source.py` | Check source-file hashes and excluded private/generated files |

For the recovery-package builder, supply `--source-root`, `--android-tree`,
`--signing-dir`, `--out`, `--scratch` and the matching `--config`.
Choose `--profile camera` or `--profile photos` to build one app.
Run `python tools/oem_addons.py --help` for the full argument list.

Use `configs/oem-addons-alpha1-24.0.json` for the Android 17 packages.
The Android 17 preparation tool has additional required inputs listed by its
`--help` output.
Input preparation and signing require manual setup with the files listed in the
selected configuration.

`SOURCE_BASELINE.json` records imported source versions and hashes.
See the release notes for phone-test results and remaining OTA/removal limits.

## Credits and contributions

The original apps and algorithms belong to Realme/Oplus and their suppliers.
Device/platform work builds on pjgowtham, LineageOS/AOSP and upstream contributors.
Project changes provide Android compatibility and optional recovery packaging.
Original licenses and notices remain in place; OEM files retain their owners' rights.

Submit source changes here and report phone problems through the release repository.

## Lens button update

The Camera compatibility JAR now prefers `google://lens`, with `googleapp://lens`
as a fallback for older Google apps. Google remains optional. Camera and Photos
keep their existing APKs and signing identities.

The Camera install ZIP recognizes the previous released Camera/Photos files.
It updates an existing Photos installation to the matching shared JAR, preserving
app data. A Camera-only installation keeps Photos absent. Unknown or modified
files stop the update. If the final Photos installation fails, keep recovery open
and install its matching Photos ZIP before rebooting. Each child installer has
its own rollback; the coordinated sequence is not atomic across power loss.

Build the Lens revision from the pinned, signed previous release archives:

```sh
python tools/oem_camera_gallery.py --base-sdk OLD_SDK --android-tree ANDROID_TREE \
  --output NEW_SDK --scratch SCRATCH --contract configs/oem-camera-lens16-23.2.json
python tools/oem_lens_bundle.py --config CONFIG --camera OLD_CAMERA_INSTALL_ZIP \
  --photos OLD_PHOTOS_INSTALL_ZIP --sdk NEW_SDK --android-tree ANDROID_TREE \
  --signing-dir SIGNING_DIR --out OUTPUT --scratch SCRATCH
```

Use `configs/oem-addons-lens16-23.2.json` for Android16 or
`configs/oem-addons-lens16-24.0.json` for Android17.

The JAR, receipt and retention hook are the only changed payload files per app.
Host tests and reproducible compilation pass. Installation and Lens-button tests
of these new packages are pending. The modern Lens link was tested on Android17.
