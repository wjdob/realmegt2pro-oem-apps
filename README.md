# Optional Realme apps

Camera and Photos/editor compatibility tools for RMX3301 on LineageOS 24.0.
Clock alpha5 is an ordinary optional APK, tested on Android17.
The ROM, Google apps and these OEM apps remain separate choices.

[Downloads and installation](https://github.com/wjdob/lineageos-realmegt2pro/tree/lineage-24.0).
[Report a user issue](https://github.com/wjdob/lineageos-realmegt2pro/issues/new/choose).
Use this repository for source changes and code review.

The imported files match the published source kit. `SOURCE_BASELINE.json` binds
each file to its original hash/revision. Original OEM APKs, libraries, model files,
signing keys and generated binaries are separate inputs and stay outside Git.
OEM components retain their owners' rights. pjgowtham, LineageOS/AOSP and Oplus
provide the underlying device/platform work; project commits provide the app
compatibility and recovery-package integration documented in the release hub.

## Development

Use Linux and Python3.12. Install `requirements-dev.txt`, then run:

```sh
python tools/validate_source.py
python -m unittest discover -s tools/tests -v
```

`tools/oem_addons.py` creates independent install/check/remove ZIPs from reviewed
input contracts. Read its `--help` and the selected `configs/oem-addons-*.json`.
`tools/prepare_oem37_sources.py` stages API37 inputs; `tools/port_stock_clock.py` prepares the unsigned Clock port. Use their --help for explicit input/output paths.
Android build tools, matching stock inputs and your own signing identity are needed
for packaging. Host tests use synthetic fixtures; runtime, OTA and removal coverage
remain separate release checks. The source split creates no newly tested app build.
