# Optional Realme apps

Camera and Photos/editor compatibility tools and recovery-package builders for
**RMX3301 on LineageOS 24.0 / Android 17**.
Clock alpha5 installs as a normal APK and has basic Android 17 test coverage.

[Downloads and installation](https://github.com/wjdob/lineageos-realmegt2pro/tree/lineage-24.0) |
[Known issues](https://github.com/wjdob/lineageos-realmegt2pro/blob/lineage-24.0/RELEASE_NOTES.md) |
[Report a problem](https://github.com/wjdob/lineageos-realmegt2pro/issues/new/choose)

These apps are optional. The base ROM and Google apps are installed separately.

[Formats candidate](FORMATS.md): HEVC settings and compatible sharing of 10-bit
HEIC photos. Format tests pass on the development phone. The Lens follow-up remains pending.
Public downloads are unchanged.

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

## Lens button follow-up

The keyed binding reaches Camera's real Lens control. Google then rejects the
launch because it lacks a calling package. The Android 17 follow-up restores
Camera's original result-launch protocol and preserves its native click checks.
Both APKs and signing identities remain unchanged from alpha4.

[Diagnosis, build recipe and pending phone tests](LENS.md). Earlier Lens16 and
formats17 source recipes remain available for reproducing those versions.
The modern Google Lens link works on the test phone; the corrected result
launch still needs installation and owner testing. Public assets are unchanged.
