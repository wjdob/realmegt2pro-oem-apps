#!/system/bin/sh
# ADDOND_VERSION=3
# OEM_RETENTION_TEMPLATE_SHA=@TEMPLATE_SHA@
# Optional OEM app retention candidate. No data/firmware/slot/BCB operations.
set -eu
umask 022
PROFILE='@PROFILE@'
POST=/postinstall
ODM_ROOT=''
OWN_ODM=0
VENDOR_ROOT=''
OWN_VENDOR=0
TRANSACTION=0
COMMITTED=0

list_files() {
    cat <<'OEM_FILES'
@FILES@
OEM_FILES
}
list_bindings() {
    cat <<'OEM_BINDINGS'
@BINDINGS@
OEM_BINDINGS
}
fail() { echo "Realme $PROFILE retention: $*" >&2; exit 1; }
policy_path() {
    case "$1" in
        product/etc/selinux/product_mac_permissions.xml|product/etc/selinux/product_seapp_contexts|product/etc/selinux/product_sepolicy.cil|product/etc/selinux/product_sepolicy_and_mapping.sha256) return 0 ;;
        *) return 1 ;;
    esac
}
expected() { list_files | awk -v name="$1" '$2 == name { print $1; found=1 } END { if (!found) exit 1 }'; }
helper_path() {
    case "$1" in system/*) printf '%s\n' "${1#system/}" ;; product/*) printf '%s\n' "$1" ;; *) return 1 ;; esac
}
source_path() {
    case "$1" in system/*) printf '%s/%s\n' "$S" "${1#system/}" ;; product/*) printf '/product/%s\n' "${1#product/}" ;; *) return 1 ;; esac
}
backup_path() { printf '%s/%s/%s\n' "$C" "$S" "$(helper_path "$1")"; }
target_path() {
    tp_rel="$1"
    tp_out=$(get_output_path "$S/$(helper_path "$tp_rel")") || return 1
    case "$tp_rel" in
        system/*) [ "$tp_out" = "$POST/$S/${tp_rel#system/}" ] || [ "$tp_out" = "$POST/system/${tp_rel#system/}" ] || return 1 ;;
        product/*) [ "$tp_out" = "$POST/product/${tp_rel#product/}" ] || return 1 ;;
        *) return 1 ;;
    esac
    printf '%s\n' "$tp_out"
}
no_links() {
    nl_path="$1"
    while [ "$nl_path" != / ] && [ -n "$nl_path" ]; do
        [ ! -L "$nl_path" ] || return 1
        nl_path=${nl_path%/*}
    done
}
hash_matches() {
    [ -f "$1" ] && no_links "$1" || return 1
    hm_value=$(sha256sum "$1") || return 1
    [ "${hm_value%% *}" = "$2" ]
}
free_kib() {
    fk_blocks=$(stat -f -c %a "$1") && fk_unit=$(stat -f -c %S "$1") || return 1
    case "$fk_blocks:$fk_unit" in *[!0-9:]*) return 1 ;; esac
    [ -n "$fk_blocks" ] && [ -n "$fk_unit" ] && [ "$fk_unit" -ge 1024 ] && [ "$((fk_unit % 1024))" = 0 ] || return 1
    printf '%s\n' "$((fk_blocks * (fk_unit / 1024)))"
}
require_environment() {
    [ "${ADDOND_VERSION:-}" = 3 ] || fail 'Reviewed V3 parent required'
    [ "$S" = /system ] && [ "$C" = /postinstall/tmp/backupdir ] || fail 'Unreviewed backuptool layout'
    [ "$(id -u)" = 0 ] && [ "$(getprop ro.product.model)" = RMX3301 ] || fail 'Wrong device or UID'
    [ "$(getenforce)" = Enforcing ] || fail 'Expected global Enforcing state'
    grep -q '^ro.build.version.sdk=36$' "$S/build.prop" || fail 'Wrong source API'
    grep -q '^ro.lineage.version=23\.2-' "$S/build.prop" /product/etc/build.prop || fail 'Wrong source ROM'
    no_links "$C" && no_links "$POST/system" && no_links "$POST/product" || fail 'Linked stage root'
    mountpoint -q "$POST/product" || fail 'V3 product mount missing'
}
release_readonly() {
    if [ "$OWN_ODM" = 1 ]; then
        umount "$ODM_ROOT" || return 1
        OWN_ODM=0
        rmdir "$ODM_ROOT" || return 1
    fi
    if [ "$OWN_VENDOR" = 1 ]; then
        umount "$VENDOR_ROOT" || return 1
        OWN_VENDOR=0
        rmdir "$VENDOR_ROOT" || return 1
    fi
}
ensure_readonly() {
    case "$1" in
        odm) [ -z "$ODM_ROOT" ] || return 0 ;;
        vendor) [ -z "$VENDOR_ROOT" ] || return 0 ;;
        *) fail 'Unreviewed read-only partition' ;;
    esac
    eo_part="$1"
    case "$(getprop ro.boot.slot_suffix)" in _a) eo_slot=_b ;; _b) eo_slot=_a ;; *) fail 'Unknown source slot' ;; esac
    eo_dev="/dev/block/mapper/$eo_part$eo_slot"
    [ -b "$eo_dev" ] || fail 'Target read-only mapping missing'
    eo_real=$(readlink -f "$eo_dev") || fail 'Cannot identify read-only mapping'
    eo_name=$(cat "/sys/class/block/${eo_real##*/}/dm/name") || fail 'Cannot read mapping identity'
    case "$eo_name" in "$eo_part$eo_slot"|"$eo_part$eo_slot-cow") ;; *) fail 'Read-only mapping identity differs' ;; esac
    eo_root="$STATE/$eo_part"
    mkdir "$eo_root" || fail 'Read-only scratch already present'
    mount -t erofs -o ro "$eo_dev" "$eo_root" || fail 'Cannot read-mount target input'
    case "$eo_part" in odm) ODM_ROOT="$eo_root";OWN_ODM=1 ;; vendor) VENDOR_ROOT="$eo_root";OWN_VENDOR=1 ;; esac
}
binding_path() {
    bp_part=${1%%/*};bp_rest=${1#*/}
    case "$bp_part" in
        system) printf '%s/system/%s\n' "$POST" "$bp_rest" ;;
        product|system_ext) mountpoint -q "$POST/$bp_part" || return 1; printf '%s/%s/%s\n' "$POST" "$bp_part" "$bp_rest" ;;
        odm) printf '%s/%s\n' "$ODM_ROOT" "$bp_rest" ;;
        vendor) printf '%s/%s\n' "$VENDOR_ROOT" "$bp_rest" ;;
        *) return 1 ;;
    esac
}
verify_source() {
    verify_peer_retention
    list_files > "$STATE/files"
    need=8192
    while read -r digest rel extra; do
        [ -z "${extra:-}" ] || fail 'Invalid source row'
        source=$(source_path "$rel") || fail 'Unknown source path'
        hash_matches "$source" "$digest" || fail "Installed bytes differ: $rel"
        bytes=$(stat -c %s "$source") || fail 'Cannot measure source file'
        need=$((need + (bytes + 1023) / 1024))
    done < "$STATE/files"
    [ "$(free_kib "$C")" -ge "$need" ] || fail 'Insufficient backup scratch'
}
verify_peer_retention() {
    case "$PROFILE" in camera) peer=photos;peer_hook=81-realme-photos.sh ;; photos) peer=camera;peer_hook=80-realme-camera.sh ;; *) fail 'Unknown selected profile' ;; esac
    peer_receipt=$(source_path "product/etc/realme-oem-addons/$peer.json") || fail 'Peer receipt path unavailable'
    peer_script="$S/addon.d/$peer_hook"
    if [ -e "$peer_receipt" ] || [ -L "$peer_receipt" ] || [ -e "$peer_script" ] || [ -L "$peer_script" ]; then
        [ -f "$peer_receipt" ] && no_links "$peer_receipt" && [ -f "$peer_script" ] && no_links "$peer_script" || fail 'Partial selected peer'
        grep -qx '# OEM_RETENTION_TEMPLATE_SHA=@TEMPLATE_SHA@' "$peer_script" || fail "Selected $peer needs a compatible retention bundle before OTA"
    fi
}
verify_backups() {
    list_files > "$STATE/files"
    while read -r digest rel; do
        hash_matches "$(backup_path "$rel")" "$digest" || fail "Backup bytes differ: $rel"
    done < "$STATE/files"
}
verify_target() {
    list_files > "$STATE/files"
    while read -r digest rel; do
        target=$(target_path "$rel") || fail 'Target outside V3 mounts'
        hash_matches "$target" "$digest" || fail "Restored bytes differ: $rel"
    done < "$STATE/files"
}
preflight_restore() {
    verify_backups
    grep -q '^ro.build.version.sdk=36$' "$POST/system/build.prop" || fail 'Wrong target API'
    grep -q '^ro.lineage.version=23\.2-' "$POST/system/build.prop" "$POST/product/etc/build.prop" || fail 'Wrong target ROM'
    list_bindings > "$STATE/bindings"
    while read -r digest rel; do
        # Function calls with mount side effects must stay in this shell.
        case "${rel%%/*}" in odm|vendor) ensure_readonly "${rel%%/*}" ;; esac
        input=$(binding_path "$rel") || fail 'Target input unavailable'
        if ! hash_matches "$input" "$digest"; then
            policy_path "$rel" && hash_matches "$input" "$(expected "$rel")" || fail "Incompatible target: $rel"
        fi
    done < "$STATE/bindings"
    release_readonly || fail 'Cannot release read-only inputs'
    ODM_ROOT='';VENDOR_ROOT=''
    system_need=8192;product_need=8192
    while read -r digest rel; do
        target=$(target_path "$rel") || fail 'Target outside V3 mounts'
        no_links "$target" || fail 'Linked target'
        if hash_matches "$target" "$digest"; then continue; fi
        if [ -e "$target" ]; then
            policy_path "$rel" || fail "Target collision: $rel"
        fi
        bytes=$(stat -c %s "$(backup_path "$rel")") || fail 'Cannot measure staged bytes'
        case "$rel" in system/*) system_need=$((system_need + (bytes + 1023) / 1024)) ;; product/*) product_need=$((product_need + (bytes + 1023) / 1024)) ;; esac
    done < "$STATE/files"
    [ "$(free_kib "$POST/system")" -ge "$system_need" ] && [ "$(free_kib "$POST/product")" -ge "$product_need" ] || fail 'Insufficient target filesystem space'
}
rollback() {
    [ -f "$STATE/journal" ] || return 0
    while read -r rel; do
        expected "$rel" >/dev/null || return 1
        target=$(target_path "$rel") || return 1
        if [ -f "$STATE/rollback/$rel" ]; then
            copy_file "$STATE/rollback/$rel" "$target" || return 1
        else
            rm -f "$target" || return 1
        fi
        rm -f "$target.realme-ota-new" || return 1
    done < "$STATE/journal"
}
finish() {
    finish_status=$?
    trap - EXIT HUP INT TERM
    set +e
    release_readonly || finish_status=1
    if [ "$TRANSACTION" = 1 ] && [ "$COMMITTED" = 0 ]; then
        rollback || { echo "Realme $PROFILE retention: rollback incomplete" >&2; finish_status=1; }
    fi
    [ "$finish_status" = 0 ] || echo "Realme $PROFILE retention FAILED; OTA success alone is not retention proof" >&2
    exit "$finish_status"
}
run_stage() {
    require_environment
    STATE="$C/.realme-$PROFILE-retention-v1"
    no_links "$STATE" || fail 'Linked retention state'
    mkdir -p "$STATE"
    trap finish EXIT
    trap 'exit 1' HUP INT TERM
    case "$1" in
        pre-backup) verify_source ;;
        backup)
            verify_source
            while read -r digest rel; do backup_file "$S/$(helper_path "$rel")" || fail 'Backup copy failed'; done < "$STATE/files"
            verify_backups
            ;;
        post-backup) verify_backups ;;
        pre-restore) preflight_restore ;;
        restore)
            preflight_restore
            : > "$STATE/journal";TRANSACTION=1
            while read -r digest rel; do
                target=$(target_path "$rel") || fail 'Target outside V3 mounts'
                if hash_matches "$target" "$digest"; then continue; fi
                if [ -f "$target" ]; then copy_file "$target" "$STATE/rollback/$rel" || fail 'Rollback staging failed'; fi
                printf '%s\n' "$rel" >> "$STATE/journal"
                copy_file "$(backup_path "$rel")" "$target.realme-ota-new" || fail 'Restore copy failed'
                hash_matches "$target.realme-ota-new" "$digest" || fail 'Restore staging hash mismatch'
                chown 0:0 "$target.realme-ota-new" && chmod 0644 "$target.realme-ota-new" || fail 'Restore metadata failed'
                mv "$target.realme-ota-new" "$target" || fail 'Restore commit failed'
            done < "$STATE/files"
            verify_target
            sync || fail 'Cannot sync restored payload'
            COMMITTED=1
            ;;
        post-restore) verify_target ;;
        *) fail 'Unknown retention stage' ;;
    esac
}
# OEM_RETENTION_ENTRY
case "${1:-}" in pre-backup|backup|post-backup|pre-restore|restore|post-restore) ;; *) exit 64 ;; esac
. /postinstall/tmp/backuptool.functions
run_stage "$1"
