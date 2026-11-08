#!/sbin/sh
# Signed Camera/Photos Lens caller update. Existing Photos is updated only when recognized.
# Child installers retain their exact device, slot, peer, space and rollback gates.
set -eu
API="$1"
OUTFD="$2"
ZIP="$3"
WORK=''
ui() { printf 'ui_print %s\nui_print\n' "$*" > "/proc/self/fd/$OUTFD"; printf '%s\n' "$*" >&2; }
die() { ui "ERROR: $*"; exit 1; }
cleanup() {
    case "$WORK" in /tmp/realme-lens-result-*) rm -rf "$WORK" ;; esac
}
run_pkg() {
    child="$1"
    unzip -p "$ZIP" "packages/$child.zip" > "$WORK/child.zip" || return 1
    expected=$(grep "  packages/$child.zip$" "$WORK/children.sha256") || return 1
    actual=$(sha256sum "$WORK/child.zip") || return 1
    [ "${actual%% *}" = "${expected%% *}" ] || return 1
    unzip -p "$WORK/child.zip" META-INF/com/google/android/update-binary > "$WORK/child.sh" || return 1
    chmod 0700 "$WORK/child.sh" || return 1
    if "$WORK/child.sh" "$API" "$OUTFD" "$WORK/child.zip" > "$WORK/result.log" 2>&1; then
        result=0
    else result=1; fi
    cat "$WORK/result.log" >&2
    rm -f "$WORK/child.zip" "$WORK/child.sh"
    return "$result"
}
peer_seen() { grep -Fxq 'Exact peer app and shared runtime verified' "$WORK/result.log"; }

orchestrate() {
    previous=''
    legacy_photos=0
    if run_pkg camera-check; then
        run_pkg camera-install || return 1
        run_pkg camera-check || return 1
        return 0
    fi
    for cohort in old3 old2 old1 old0; do
        if run_pkg "$cohort-camera-check"; then
            previous="$cohort"
            if peer_seen; then
                run_pkg "$cohort-photos-check" || { ui 'Unsupported Photos revision; no changes made'; return 1; }
                legacy_photos=1
            fi
            break
        fi
    done
    [ -n "$previous" ] || { ui 'Unknown or mixed app state; no changes made'; return 1; }
    if [ "$legacy_photos" = 1 ]; then
        run_pkg "$previous-photos-remove" || return 1
    fi
    if ! run_pkg camera-install; then
        if [ "$legacy_photos" = 1 ]; then
            if run_pkg "$previous-photos-install"; then
                ui 'Camera update failed; previous Photos files restored. App data was retained.'
            else ui 'STOP: Photos restoration failed. Keep recovery open and save its log.'; fi
        fi
        return 1
    fi
    if [ "$legacy_photos" = 1 ]; then
        if ! run_pkg photos-install; then
            ui 'Camera updated; Photos installation is incomplete. Sideload matching Photos before reboot.'
            ui 'Existing Photos data and saved media are retained. Save the recovery log.'
            return 1
        fi
        run_pkg photos-check || return 1
    fi
    run_pkg camera-check || return 1
    ui 'Camera/Photos Lens caller update complete. Existing app data is retained.'
}

# HOST_TEST_FUNCTIONS_END
[ "$(getprop ro.bootmode)" = recovery ] || die 'Run from recovery'
[ "$(getprop ro.product.model)" = RMX3301 ] || die 'RMX3301 required'
for tool in unzip sha256sum grep stat mktemp chmod cat rm; do
    command -v "$tool" >/dev/null 2>&1 || die "Missing recovery command: $tool"
done
WORK=$(mktemp -d /tmp/realme-lens-result-XXXXXX)
trap cleanup EXIT
unzip -p "$ZIP" children.sha256 > "$WORK/children.sha256" || die 'Missing child manifest'
expected_index='@INDEX_SHA@'
actual=$(sha256sum "$WORK/children.sha256")
[ "${actual%% *}" = "$expected_index" ] || die 'Child manifest differs'
# Check all nested packages before the first writable child operation.
while read -r digest member extra; do
    [ -z "${extra:-}" ] || die 'Invalid child manifest row'
    case "$member" in @CHILD_CASE@) ;; *) die 'Unexpected child package' ;; esac
    actual=$(unzip -p "$ZIP" "$member" | sha256sum)
    [ "${actual%% *}" = "$digest" ] || die 'Child package differs'
done < "$WORK/children.sha256"
blocks=$(stat -f -c %a "$WORK")
unit=$(stat -f -c %S "$WORK")
case "$blocks:$unit" in *[!0-9:]*|:*) die 'Cannot size recovery space' ;; esac
[ "$unit" -ge 1024 ] && [ "$((unit % 1024))" = 0 ] || die 'Unsupported recovery block size'
free_kib=$((blocks * (unit / 1024)))
[ "$free_kib" -ge @SCRATCH_KIB@ ] || die 'Insufficient scratch for coordinated update; nothing changed'
orchestrate || exit 1
