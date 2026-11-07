#!/sbin/sh
# RMX3301 optional OEM apps. No slot selection, block writes, resize or data erase.
set -eu
umask 022
PROFILE='@PROFILE@'
APP_PARTITION='@APP_PARTITION@'
APP_CLASS='@APP_CLASS@'
LEGACY_APP_ROOT='@LEGACY_APP_ROOT@'
PLATFORM_FRAMEWORK_SHA='@PLATFORM_FRAMEWORK_SHA@'
TITLE='@TITLE@'
ACTION='@ACTION@'
APPDIR='@APPDIR@'
HOOK='@HOOK@'
OTHER_APPDIR='@OTHER_APPDIR@'
CERT_SHA='@CERT_SHA@'
PRODUCT_KIB='@PRODUCT_KIB@'
SYSTEM_KIB='@SYSTEM_KIB@'
FULL_PAYLOAD_KIB=$((PRODUCT_KIB + SYSTEM_KIB))
HAS_POLICY='@HAS_POLICY@'
SHARED_POLICY='@SHARED_POLICY@'
HAS_UPGRADES='@HAS_UPGRADES@'
HAS_ASSETS='@HAS_ASSETS@'
HAS_NATIVE_REQUIREMENTS='@HAS_NATIVE_REQUIREMENTS@'
NATIVE_REQUIREMENT_COUNT='@NATIVE_REQUIREMENT_COUNT@'
HELPER_SHA='@HELPER_SHA@'
HELPER_LIBRARIES_SHA='@HELPER_LIBRARIES_SHA@'
ISOLATED_PHOTOS='@ISOLATED_PHOTOS@'
HAS_PEERS='@HAS_PEERS@'
PEER_INDEX_SHA='@PEER_INDEX_SHA@'
PEER_PROFILE='@PEER_PROFILE@'
PEER_APP_ROOT='@PEER_APP_ROOT@'
PEER_ALLOWED_CASE='@PEER_ALLOWED_CASE@'
SHARED_RUNTIME_CASE='@SHARED_RUNTIME_CASE@'
LEGACY_SHARED_CASE='@LEGACY_SHARED_CASE@'
FD="/proc/self/fd/$2"
ZIP="$3"
WORK=''
MOUNTED_SYSTEM=0
MOUNTED_PRODUCT=0
COMMITTED=0
WRITABLE=0
DURABILITY_PENDING=0
KEEP_SHARED=0
PEER_MATCHED=0
PEER_MANIFEST=''
MOUNTED_EXTRAS=''
UPGRADING=0
INSTALLED_MANIFEST=''
ALREADY_INSTALLED=0
ui() {
    printf 'ui_print %s\nui_print\n' "$*" > "$FD"
    printf '%s\n' "$*" >&2
}
die() { ui "ERROR: $*"; exit 1; }
policy_patch() {
    [ "$HAS_POLICY" = 1 ] || return 1
    [ "$PROFILE" = camera ] || [ "$SHARED_POLICY" = 1 ] || return 1
    case "$1" in
        product/etc/selinux/product_mac_permissions.xml|product/etc/selinux/product_seapp_contexts|product/etc/selinux/product_sepolicy.cil|product/etc/selinux/product_sepolicy_and_mapping.sha256) return 0 ;;
        *) return 1 ;;
    esac
}
policy_before() {
    while read -r post pre path; do
        if [ "$path" = "$1" ]; then printf '%s\n' "$pre"; return 0; fi
    done < "$WORK/policy/patches.tsv"
    return 1
}
file_context() {
    case "$1" in
        system/lib64/*) printf '%s\n' u:object_r:system_lib_file:s0 ;;
        product/etc/selinux/product_mac_permissions.xml) printf '%s\n' u:object_r:mac_perms_file:s0 ;;
        product/etc/selinux/product_seapp_contexts) printf '%s\n' u:object_r:seapp_contexts_file:s0 ;;
        *) printf '%s\n' u:object_r:system_file:s0 ;;
    esac
}
prepare_policy() {
    [ "$HAS_POLICY" = 1 ] || return 0
    [ "$PROFILE" = camera ] || [ "$SHARED_POLICY" = 1 ] || die 'Unreviewed policy profile'
    unzip -o "$ZIP" 'policy/*' -d "$WORK" >/dev/null || die 'Missing policy rollback support'
    while read -r hash rel; do
        case "$rel" in product/etc/realme-oem-addons/camera-policy/*)
            source="$WORK/policy/${rel#product/etc/realme-oem-addons/camera-policy/}"
            actual=$(sha256sum "$source") || die 'Missing policy support file'
            [ "${actual%% *}" = "$hash" ] || die 'Policy support hash mismatch' ;;
        esac
    done < "$WORK/manifest.sha256"
    rows=0
    : > "$WORK/policy-seen"
    while read -r post pre rel extra; do
        [ -z "${extra:-}" ] && policy_patch "$rel" || die 'Unexpected policy transaction path'
        ! grep -qx "$rel" "$WORK/policy-seen" || die 'Duplicate policy transaction path'
        printf '%s\n' "$rel" >> "$WORK/policy-seen"
        for digest in "$post" "$pre"; do
            [ "${#digest}" = 64 ] || die 'Invalid policy digest'
            case "$digest" in *[!0-9a-f]*) die 'Invalid policy digest' ;; esac
        done
        grep -qx "$post  $rel" "$WORK/manifest.sha256" || die 'Policy postimage manifest differs'
        actual=$(sha256sum "$WORK/policy/preimages/${rel##*/}") || die 'Policy preimage missing'
        [ "${actual%% *}" = "$pre" ] || die 'Policy preimage hash mismatch'
        actual=$(sha256sum "$WORK/policy/postimages/${rel##*/}") || die 'Policy postimage missing'
        [ "${actual%% *}" = "$post" ] || die 'Policy postimage hash mismatch'
        rows=$((rows + 1))
    done < "$WORK/policy/patches.tsv"
    [ "$rows" = 4 ] || die 'Incomplete policy transaction'
}
release_policy_inputs() {
    remaining=''
    for part in $MOUNTED_EXTRAS; do
        if ! umount "$WORK/$part-mount"; then remaining="$remaining $part"; fi
    done
    MOUNTED_EXTRAS="$remaining"
    [ -z "$MOUNTED_EXTRAS" ]
}
verify_logical_identity() {
    case "$1" in system|product|system_ext|vendor|odm) ;; *) die 'Unreviewed logical partition' ;; esac
    case "$SLOT" in _a|_b) ;; *) die 'Unrecognized logical slot' ;; esac
    li_dev="/dev/block/mapper/$1$SLOT"
    [ -b "$li_dev" ] || die 'Logical partition missing'
    li_real=$(readlink -f "$li_dev") || die 'Cannot resolve logical device'
    [ "$(cat "/sys/class/block/${li_real##*/}/dm/name")" = "$1$SLOT" ] || die 'Logical partition identity mismatch'
}
verify_native_requirements() {
    [ "$HAS_NATIVE_REQUIREMENTS" = 1 ] || return 0
    nr_own_mount=0
    if [ "$HAS_POLICY" = 0 ]; then
        nr_dev="/dev/block/mapper/odm$SLOT"
        verify_logical_identity odm
        mkdir "$WORK/odm-mount"
        mount -t erofs -o ro "$nr_dev" "$WORK/odm-mount" || die 'Cannot read-mount native input'
        MOUNTED_EXTRAS="$MOUNTED_EXTRAS odm"; nr_own_mount=1
    fi
    unzip -p "$ZIP" native-requirements.sha256 > "$WORK/native-requirements.sha256" || die 'Missing native runtime bindings'
    : > "$WORK/native-seen"
    nr_count=0
    while read -r nr_hash nr_rel nr_extra; do
        [ -z "${nr_extra:-}" ] && [ "${#nr_hash}" = 64 ] || die 'Invalid native runtime binding'
        case "$nr_hash" in *[!0-9a-f]*) die 'Invalid native runtime digest' ;; esac
        case "$nr_rel" in
            system/lib64/libnativewindow.so) nr_source="$SYSTEM/lib64/libnativewindow.so" ;;
            odm/lib64/libAlgoInterface.so) nr_source="$WORK/odm-mount/lib64/libAlgoInterface.so" ;;
            odm/lib64/libmsnativefilter.so) nr_source="$WORK/odm-mount/lib64/libmsnativefilter.so" ;;
            odm/lib64/libAlgoProcess.so) [ "$PROFILE" = photos ] && [ "$SHARED_POLICY" = 1 ] || die 'Unreviewed editor backend'; nr_source="$WORK/odm-mount/lib64/libAlgoProcess.so" ;;
            *) die 'Unreviewed native runtime path' ;;
        esac
        ! grep -Fxq "$nr_rel" "$WORK/native-seen" || die 'Duplicate native runtime binding'
        printf '%s\n' "$nr_rel" >> "$WORK/native-seen"
        no_links "$nr_source" || die 'Native runtime symlink refused'
        nr_actual=$(sha256sum "$nr_source") || die 'Native runtime input missing'
        [ "${nr_actual%% *}" = "$nr_hash" ] || die "Camera needs matching bundled ROM: $nr_rel"
        nr_count=$((nr_count + 1))
    done < "$WORK/native-requirements.sha256"
    [ "$nr_count" = "$NATIVE_REQUIREMENT_COUNT" ] || die 'Incomplete native runtime bindings'
    if [ "$nr_own_mount" = 1 ]; then release_policy_inputs || die 'Cannot release native inputs'; fi
}
verify_policy_inputs() {
    if [ "$HAS_POLICY" = 0 ]; then verify_native_requirements; return; fi
    for part in system_ext vendor odm; do
        dev="/dev/block/mapper/${part}${SLOT}"
        verify_logical_identity "$part"
        mkdir "$WORK/$part-mount"
        if [ "$part" = system_ext ]; then
            mount -t ext4 -o ro,noload "$dev" "$WORK/$part-mount" || die 'Cannot read-mount system_ext policy'
        else
            mount -t erofs -o ro "$dev" "$WORK/$part-mount" || die 'Cannot read-mount vendor/odm policy'
        fi
        MOUNTED_EXTRAS="$MOUNTED_EXTRAS $part"
    done
    while read -r hash rel extra; do
        [ -z "${extra:-}" ] || die 'Invalid policy input binding'
        case "$rel" in *..*|*[!A-Za-z0-9_./+-]*) die 'Unsafe policy input path' ;; esac
        case "$rel" in
            system/*|product/*) source=$(target "$rel") ;;
            system_ext/*|vendor/*|odm/*) part="${rel%%/*}"; source="$WORK/$part-mount/${rel#*/}" ;;
            *) die 'Unexpected policy input partition' ;;
        esac
        actual=$(sha256sum "$source") || die 'Cannot read policy compiler input'
        actual="${actual%% *}"
        if [ "$actual" != "$hash" ]; then
            # Only a reviewed already-installed postimage can differ from baseline.
            policy_patch "$rel" || die "Policy compiler input differs: $rel"
            if ! grep -q "^$actual $hash $rel$" "$WORK/policy/patches.tsv"; then
                [ "$UPGRADING" = 1 ] && [ "$(manifest_hash "$INSTALLED_MANIFEST" "$rel")" = "$actual" ] || die 'Unknown patched compiler input'
            fi
        fi
    done < "$WORK/policy/inputs.sha256"
    verify_native_requirements
    release_policy_inputs || die 'Cannot release read-only policy inputs'
}
target() {
    case "$1" in
        product/*) printf '%s/%s\n' "$PRODUCT" "${1#product/}" ;;
        system/*) printf '%s/%s\n' "$SYSTEM" "${1#system/}" ;;
        *) return 1 ;;
    esac
}
shared_path() {
    case "$1" in @SHARED_CASE@) return 0 ;; *) return 1 ;; esac
}
asset_path() {
    case "$1" in @ASSET_CASE@) return 0 ;; *) return 1 ;; esac
}
asset_root() { if [ "$HAS_ASSETS" = 1 ]; then printf '%s\n' "$SYSTEM/etc/realme-lut"; fi; }
app_root() { target "$APP_PARTITION/$APP_CLASS/$APPDIR"; }
legacy_app_roots() {
    for la_rel in $LEGACY_APP_ROOT; do target "$la_rel"; done
}
verify_platform_framework() {
    [ -n "$PLATFORM_FRAMEWORK_SHA" ] || return 0
    actual=$(sha256sum "$SYSTEM/framework/oplus-fwk.jar") || die 'Platform framework missing'
    [ "${actual%% *}" = "$PLATFORM_FRAMEWORK_SHA" ] || die 'Unreviewed Camera platform framework'
}
shared_runtime_path() { case "$1" in @SHARED_RUNTIME_CASE@) return 0 ;; *) return 1 ;; esac; }
legacy_shared_path() { case "$1" in @LEGACY_SHARED_CASE@) return 0 ;; *) return 1 ;; esac; }
peer_allowed() { case "$1" in @PEER_ALLOWED_CASE@) return 0 ;; *) return 1 ;; esac; }
peer_owns() {
    [ "$HAS_PEERS" = 1 ] && [ "$PEER_MATCHED" = 1 ] && shared_runtime_path "$1" || return 1
    po_hash=$(manifest_hash "$PEER_MANIFEST" "$1") || return 1
    [ "$po_hash" = "$(manifest_hash "$WORK/manifest.sha256" "$1")" ]
}
retain_shared() {
    if [ "$HAS_PEERS" = 1 ]; then peer_owns "$1"; return; fi
    [ "$KEEP_SHARED" = 1 ] && shared_path "$1"
}
select_shared_retention() {
    KEEP_SHARED=0
    [ "$HAS_PEERS" = 0 ] || return 0
    if [ "$ACTION" = remove ] && [ -f "$PRODUCT/app/$OTHER_APPDIR/base.apk" ]; then
        KEEP_SHARED=1
        ui "Retaining shared SDK for installed $OTHER_APPDIR"
    fi
}
allowed() {
    case "$1" in *..*|*[!A-Za-z0-9_./+-]*) return 1 ;; esac
    case "$1" in system/etc/realme-lut/*) asset_path "$1"; return ;; esac
    if shared_path "$1" || legacy_shared_path "$1"; then return 0; fi
    if policy_patch "$1"; then return 0; fi
    for al_root in $LEGACY_APP_ROOT; do
        case "$1" in "$al_root/"*) return 0 ;; esac
    done
    if [ "$APP_CLASS" = priv-app ] && [ "$PROFILE" = camera ]; then
        case "$1" in system/etc/permissions/privapp-permissions-realme-camera.xml) return 0 ;; esac
    fi
    if [ "$HAS_POLICY" = 1 ] && { [ "$PROFILE" = camera ] || [ "$SHARED_POLICY" = 1 ]; }; then
        case "$1" in product/etc/realme-oem-addons/camera-policy/*) return 0 ;; esac
    fi
    case "$1" in
        "$APP_PARTITION/$APP_CLASS/$APPDIR/"*|"product/etc/sysconfig/realme-$PROFILE.xml"|"product/etc/realme-oem-addons/$PROFILE.json"|"system/addon.d/$HOOK") return 0 ;;
        *) return 1 ;;
    esac
}
no_links() {
    path="$1"
    while [ "$path" != "$WORK" ]; do
        [ ! -L "$path" ] || return 1
        case "$path" in "$WORK"/*) path="${path%/*}" ;; *) return 1 ;; esac
    done
}
manifest_hash() {
    mh_file="$1"; mh_requested="$2"
    while read -r mh_hash mh_rel mh_extra; do
        if [ "$mh_rel" = "$mh_requested" ] && [ -z "${mh_extra:-}" ]; then
            printf '%s\n' "$mh_hash"; return 0
        fi
    done < "$mh_file"
    return 1
}
select_peer() {
    [ "$HAS_PEERS" = 1 ] || return 0
    unzip -o "$ZIP" 'peers/*' -d "$WORK" >/dev/null || die 'Missing peer ownership support'
    sp_index=$(sha256sum "$WORK/peers/index.sha256") || die 'Missing peer index'
    [ "${sp_index%% *}" = "$PEER_INDEX_SHA" ] || die 'Peer index differs'
    sp_count=0
    while read -r sp_digest sp_version sp_extra; do
        [ -z "${sp_extra:-}" ] && [ "${#sp_digest}" = 64 ] || die 'Invalid peer index'
        case "$sp_digest" in *[!0-9a-f]*) die 'Invalid peer index digest' ;; esac
        case "$sp_version" in "$PEER_PROFILE"-[a-z0-9]*) ;; *) die 'Invalid peer identity' ;; esac
        case "$sp_version" in *[!a-z0-9-]*) die 'Unsafe peer version' ;; esac
        [ "${#sp_version}" -le 40 ] || die 'Oversized peer version'
        sp_file="$WORK/peers/$sp_version.sha256"
        sp_actual=$(sha256sum "$sp_file") || die 'Missing peer manifest'
        [ "${sp_actual%% *}" = "$sp_digest" ] || die 'Peer manifest hash differs'
        : > "$WORK/peer-seen"
        while read -r sp_hash sp_rel sp_extra; do
            [ -z "${sp_extra:-}" ] && peer_allowed "$sp_rel" || die 'Unreviewed peer path'
            [ "${#sp_hash}" = 64 ] || die 'Invalid peer file digest'
            case "$sp_hash" in *[!0-9a-f]*) die 'Invalid peer file digest' ;; esac
            ! grep -Fxq "$sp_rel" "$WORK/peer-seen" || die 'Duplicate peer path'
            printf '%s\n' "$sp_rel" >> "$WORK/peer-seen"
        done < "$sp_file"
        [ -s "$WORK/peer-seen" ] || die 'Empty peer manifest'
        sp_count=$((sp_count+1)); [ "$sp_count" -le 8 ] || die 'Too many peer manifests'
    done < "$WORK/peers/index.sha256"
    [ "$sp_count" -gt 0 ] || die 'Empty peer index'
    sp_roots=''
    for sp_relroot in $PEER_APP_ROOT; do
        sp_root=$(target "$sp_relroot") || die 'Invalid peer root'
        sp_roots="$sp_roots $sp_root"
    done
    sp_present=0
    for sp_path in $sp_roots "$PRODUCT/etc/realme-oem-addons/$PEER_PROFILE.json" "$SYSTEM/addon.d/80-realme-$PEER_PROFILE.sh" "$SYSTEM/addon.d/81-realme-$PEER_PROFILE.sh"; do
        if [ -e "$sp_path" ] || [ -L "$sp_path" ]; then sp_present=1; fi
    done
    # A legacy/unknown placement is not silently ignored as an absent peer.
    for sp_path in "$PRODUCT/app/$OTHER_APPDIR" "$SYSTEM/app/$OTHER_APPDIR" "$SYSTEM/priv-app/$OTHER_APPDIR" "$PRODUCT"/app/RealmeStockPhotos-*; do
        [ "$PEER_PROFILE" = photos ] || case "$sp_path" in "$PRODUCT"/app/RealmeStockPhotos-*) continue ;; esac
        sp_known=0
        for sp_root in $sp_roots; do [ "$sp_path" != "$sp_root" ] || sp_known=1; done
        if [ "$sp_known" = 0 ] && { [ -e "$sp_path" ] || [ -L "$sp_path" ]; }; then die 'Unreviewed peer placement'; fi
    done
    [ "$sp_present" = 1 ] || return 0
    while read -r sp_digest sp_version; do
        sp_file="$WORK/peers/$sp_version.sha256"; sp_match=1
        while read -r sp_hash sp_rel; do
            sp_dest=$(target "$sp_rel") || die 'Invalid peer target'
            if ! no_links "$sp_dest" || [ ! -f "$sp_dest" ]; then sp_match=0; break; fi
            sp_actual=$(sha256sum "$sp_dest") || die 'Cannot inspect peer file'
            if [ "${sp_actual%% *}" != "$sp_hash" ]; then sp_match=0; break; fi
        done < "$sp_file"
        [ "$sp_match" = 1 ] || continue
        [ "$PEER_MATCHED" = 0 ] || die 'Ambiguous installed peer'
        PEER_MATCHED=1; PEER_MANIFEST="$sp_file"
    done < "$WORK/peers/index.sha256"
    [ "$PEER_MATCHED" = 1 ] || die 'Unknown, partial or changed peer; update/remove its matching bundle first'
    sp_policy_root=''
    if [ "$PEER_PROFILE" = camera ]; then sp_policy_root="$PRODUCT/etc/realme-oem-addons/camera-policy"; fi
    for sp_tree in $sp_roots "$SYSTEM/etc/realme-lut" "$sp_policy_root"; do
        [ -n "$sp_tree" ] || continue
        [ -e "$sp_tree" ] || continue
        no_links "$sp_tree" || die 'Peer tree symlink refused'
        find "$sp_tree" ! -type d -print > "$WORK/peer-tree" || die 'Cannot inspect peer tree'
        while IFS= read -r sp_dest; do
            no_links "$sp_dest" && [ -f "$sp_dest" ] || die 'Unknown peer object'
            case "$sp_dest" in
                "$PRODUCT/"*) sp_rel="product/${sp_dest#"$PRODUCT/"}" ;;
                "$SYSTEM/"*) sp_rel="system/${sp_dest#"$SYSTEM/"}" ;;
                *) die 'Peer tree escaped mounts' ;;
            esac
            manifest_hash "$PEER_MANIFEST" "$sp_rel" >/dev/null || die 'Unknown peer file'
        done < "$WORK/peer-tree"
    done
    ui 'Exact peer app and shared runtime verified'
}
verify_legacy_sdk_state() {
    [ "$HAS_PEERS" = 1 ] || return 0
    for ls_rel in system/framework/realme-camera-unit-sdk.jar system/etc/permissions/realme-camera-unit-sdk.xml; do
        ls_dest=$(target "$ls_rel") || die 'Invalid legacy runtime path'
        if [ -e "$ls_dest" ] || [ -L "$ls_dest" ]; then
            [ "$PROFILE" = camera ] && [ "$UPGRADING" = 1 ] || die 'Legacy SDK registration remains; use its matching upgrade/removal first'
            ls_hash=$(manifest_hash "$INSTALLED_MANIFEST" "$ls_rel") || die 'Legacy SDK is not owned by the verified predecessor'
            no_links "$ls_dest" && [ -f "$ls_dest" ] || die 'Legacy SDK object refused'
            ls_actual=$(sha256sum "$ls_dest") || die 'Cannot inspect legacy SDK'
            [ "${ls_actual%% *}" = "$ls_hash" ] || die 'Legacy SDK hash differs'
        fi
    done
}
validate_manifest() {
    : > "$WORK/seen-paths"
    while read -r vm_hash vm_rel vm_extra; do
        [ -z "${vm_extra:-}" ] && allowed "$vm_rel" || die 'Unrecognized predecessor ownership path'
        [ "${#vm_hash}" = 64 ] || die 'Invalid predecessor digest'
        case "$vm_hash" in *[!0-9a-f]*) die 'Invalid predecessor digest' ;; esac
        ! grep -Fxq "$vm_rel" "$WORK/seen-paths" || die 'Duplicate predecessor path'
        printf '%s\n' "$vm_rel" >> "$WORK/seen-paths"
    done < "$1"
    [ -s "$WORK/seen-paths" ] || die 'Empty predecessor manifest'
}
owned_tree_matches() {
    ot_manifest="$1"
    if [ "$PROFILE" = photos ]; then
        for ot_candidate in "$PRODUCT"/app/RealmeStockPhotos-*; do
            [ -e "$ot_candidate" ] || [ -L "$ot_candidate" ] || continue
            ot_known=0
            for ot_knownroot in "$(app_root)" $(legacy_app_roots); do
                if [ "$ot_candidate" = "$ot_knownroot" ]; then ot_known=1; break; fi
            done
            [ "$ot_known" = 1 ] || return 1
        done
    fi
    ot_policy_root=''
    if [ "$HAS_POLICY" = 1 ]; then ot_policy_root="$PRODUCT/etc/realme-oem-addons/camera-policy"; fi
    for ot_root in "$(app_root)" $(legacy_app_roots) "$(asset_root)" "$ot_policy_root"; do
        [ -e "$ot_root" ] || [ -L "$ot_root" ] || continue
        no_links "$ot_root" || return 1
        find "$ot_root" ! -type d -print > "$WORK/owned-tree" || return 1
        while IFS= read -r ot_dest; do
            no_links "$ot_dest" && [ -f "$ot_dest" ] || return 1
            case "$ot_dest" in
                "$PRODUCT/"*) ot_rel="product/${ot_dest#"$PRODUCT/"}" ;;
                "$SYSTEM/"*) ot_rel="system/${ot_dest#"$SYSTEM/"}" ;;
                *) return 1 ;;
            esac
            manifest_hash "$ot_manifest" "$ot_rel" >/dev/null || peer_owns "$ot_rel" || return 1
        done < "$WORK/owned-tree"
    done
}
verify_existing_files() {
while read -r hash rel extra; do
    [ -z "${extra:-}" ] && allowed "$rel" || die 'Unexpected manifest path'
    [ "${#hash}" = 64 ] || die 'Invalid manifest digest'
    case "$hash" in *[!0-9a-f]*) die 'Invalid manifest digest' ;; esac
    dest=$(target "$rel")
    no_links "$dest" || die 'Symlink in destination path'
    if [ "$ACTION" = remove ] && retain_shared "$rel"; then continue; fi
    [ ! -e "$dest" ] || [ -f "$dest" ] || die 'Destination is not a regular file'
    if [ -f "$dest" ]; then
        actual=$(sha256sum "$dest")
        actual="${actual%% *}"
        if [ "$actual" != "$hash" ]; then
            if [ "$UPGRADING" = 1 ] && [ "$(manifest_hash "$INSTALLED_MANIFEST" "$rel" || true)" = "$actual" ]; then :
            else
                policy_patch "$rel" && [ "$actual" = "$(policy_before "$rel")" ] || die "Existing owned file differs: $rel"
            fi
        fi
    elif policy_patch "$rel"; then
        die 'Original policy file missing'
    fi
done < "$WORK/manifest.sha256"
}
verify_owned_tree() {
    [ "$HAS_PEERS" = 1 ] || return 0
    if [ "$UPGRADING" = 1 ]; then
        owned_tree_matches "$INSTALLED_MANIFEST" || die 'Unknown predecessor files'
    else
        owned_tree_matches "$WORK/manifest.sha256" || die 'Unknown owned files'
    fi
}
known_install_matches() {
    km_manifest="$1"
    while read -r km_hash km_rel; do
        km_dest=$(target "$km_rel") || return 1
        no_links "$km_dest" && [ -f "$km_dest" ] || return 1
        km_actual=$(sha256sum "$km_dest") || return 1
        [ "${km_actual%% *}" = "$km_hash" ] || return 1
    done < "$km_manifest"
    # Refuse partial mixes of a predecessor and a newer candidate.
    while read -r km_hash km_rel; do
        if ! manifest_hash "$km_manifest" "$km_rel" >/dev/null; then
            if peer_owns "$km_rel"; then continue; fi
            km_dest=$(target "$km_rel") || return 1
            [ ! -e "$km_dest" ] && [ ! -L "$km_dest" ] || return 1
        fi
    done < "$WORK/manifest.sha256"
    owned_tree_matches "$km_manifest"
}
select_upgrade() {
    [ "$HAS_UPGRADES" = 1 ] || return 0
    [ "$ACTION" != remove ] || die 'Upgrade profile mismatch'
    if [ "$HAS_PEERS" = 0 ]; then
        [ ! -e "$PRODUCT/app/$OTHER_APPDIR/base.apk" ] || die 'Unreviewed Photos/shared SDK combination'
    fi
    unzip -o "$ZIP" 'upgrades/*' -d "$WORK" >/dev/null || die 'Missing predecessor manifests'
    [ -s "$WORK/upgrades/index.sha256" ] || die 'Missing predecessor index'
    su_count=0
    while read -r su_hash su_version su_extra; do
        [ -z "${su_extra:-}" ] && [ -n "$su_version" ] || die 'Invalid predecessor index'
        case "$su_version" in *[!a-z0-9-]*|-*) die 'Unsafe predecessor version' ;; esac
        [ "${#su_hash}" = 64 ] && [ "${#su_version}" -le 32 ] || die 'Invalid predecessor index size'
        case "$su_hash" in *[!0-9a-f]*) die 'Invalid predecessor index digest' ;; esac
        su_file="$WORK/upgrades/$su_version.sha256"
        su_actual=$(sha256sum "$su_file") || die 'Missing predecessor manifest'
        [ "${su_actual%% *}" = "$su_hash" ] || die 'Predecessor manifest hash mismatch'
        validate_manifest "$su_file"
        su_count=$((su_count + 1)); [ "$su_count" -le 8 ] || die 'Too many predecessors'
    done < "$WORK/upgrades/index.sha256"
    if known_install_matches "$WORK/manifest.sha256"; then
        ALREADY_INSTALLED=1
        ui 'This exact Camera version is already installed'; return 0
    fi
    while read -r su_hash su_version; do
        if known_install_matches "$WORK/upgrades/$su_version.sha256"; then
            [ "$UPGRADING" = 0 ] || die 'Ambiguous installed Camera version'
            UPGRADING=1; INSTALLED_MANIFEST="$WORK/upgrades/$su_version.sha256"
            read -r su_system su_product su_extra < "$WORK/upgrades/$su_version.space" || die 'Missing predecessor space budget'
            [ -n "$su_system" ] && [ -n "$su_product" ] && [ -z "${su_extra:-}" ] || die 'Invalid predecessor space budget'
            case "$su_system:$su_product" in *[!0-9:]*) die 'Invalid predecessor space numbers' ;; esac
            [ "${#su_system}" -le 8 ] && [ "${#su_product}" -le 8 ] || die 'Oversized predecessor space numbers'
            [ "$su_system" -ge 4096 ] && [ "$su_system" -le "$SYSTEM_KIB" ] &&
                [ "$su_product" -ge 16384 ] && [ "$su_product" -le "$PRODUCT_KIB" ] || die 'Unsafe predecessor space budget'
            SYSTEM_KIB=$su_system; PRODUCT_KIB=$su_product
            ui "Recognized Camera predecessor $su_version; staging a reversible upgrade"
        fi
    done < "$WORK/upgrades/index.sha256"
    if [ "$UPGRADING" = 0 ]; then
        # Fresh install requires every non-policy file in all known manifests absent.
        for su_file in "$WORK/manifest.sha256" "$WORK"/upgrades/*.sha256; do
            case "$su_file" in */index.sha256) continue ;; esac
            while read -r su_hash su_rel; do
                policy_patch "$su_rel" && continue
                if peer_owns "$su_rel"; then continue; fi
                su_dest=$(target "$su_rel") || die 'Unknown fresh-install destination'
                [ ! -e "$su_dest" ] && [ ! -L "$su_dest" ] || die 'Unknown or mixed Camera install; no files changed'
            done < "$su_file"
        done
        owned_tree_matches "$WORK/manifest.sha256" || die 'Unknown Camera files; no cleanup guessed'
    fi
}
select_space_budget() {
    # Recognition and idempotent installs do not need another full APK copy.
    # Keep full fresh-install budgets unless the complete installed state matches.
    # FULL_PAYLOAD_KIB remains unchanged for recovery RAM/rollback staging.
    if [ "$ALREADY_INSTALLED" = 1 ] || {
        [ "$ACTION" = check ] && known_install_matches "$WORK/manifest.sha256"
    }; then
        SYSTEM_KIB=4096
        PRODUCT_KIB=16384
    fi
}
stage_upgrade_rollback() {
    [ "$UPGRADING" = 1 ] || return 0
    while read -r ur_hash ur_rel; do
        ur_dest=$(target "$ur_rel")
        mkdir -p "$WORK/rollback/${ur_rel%/*}"
        cp -p "$ur_dest" "$WORK/rollback/$ur_rel" || die 'Cannot stage existing Camera rollback'
        ur_actual=$(sha256sum "$WORK/rollback/$ur_rel")
        [ "${ur_actual%% *}" = "$ur_hash" ] || die 'Rollback preimage changed'
    done < "$INSTALLED_MANIFEST"
    # A policyless Gallery predecessor did not own these existing OS files.
    # Snapshot their verified actual preimages too; rollback must never delete
    # a baseline policy file merely because it was absent from the old app list.
    if [ "$HAS_POLICY" = 1 ]; then
        while read -r ur_post ur_pre ur_rel; do
            manifest_hash "$INSTALLED_MANIFEST" "$ur_rel" >/dev/null && continue
            ur_dest=$(target "$ur_rel")
            ur_actual=$(sha256sum "$ur_dest") || die 'Cannot stage baseline policy rollback'
            case "${ur_actual%% *}" in "$ur_pre"|"$ur_post") ;; *) die 'Baseline policy changed before rollback staging' ;; esac
            mkdir -p "$WORK/rollback/${ur_rel%/*}"
            cp -p "$ur_dest" "$WORK/rollback/$ur_rel" || die 'Cannot stage baseline policy rollback'
            ur_copy=$(sha256sum "$WORK/rollback/$ur_rel")
            [ "${ur_copy%% *}" = "${ur_actual%% *}" ] || die 'Baseline policy rollback copy differs'
        done < "$WORK/policy/patches.tsv"
    fi
}
stage_remove_rollback() {
    while read -r hash rel; do
        if retain_shared "$rel"; then continue; fi
        dest=$(target "$rel")
        if [ -f "$dest" ]; then
            mkdir -p "$WORK/rollback/${rel%/*}"
            cp -p "$dest" "$WORK/rollback/$rel" || die 'Rollback staging failed'
        fi
    done < "$WORK/manifest.sha256"
}
remove_upgrade_stale() {
    [ "$UPGRADING" = 1 ] || return 0
    while read -r us_hash us_rel; do
        if ! manifest_hash "$WORK/manifest.sha256" "$us_rel" >/dev/null; then
            us_dest=$(target "$us_rel")
            printf '%s\n' "$us_rel" >> "$WORK/journal"
            rm "$us_dest" || die 'Cannot remove recognized stale dependency'
        fi
    done < "$INSTALLED_MANIFEST"
}
verify_rom() {
    [ -f "$SYSTEM/build.prop" ] || die 'Expected system-as-root layout missing'
    grep -q '^ro.lineage.version=23\.2-' "$SYSTEM/build.prop" "$PRODUCT/etc/build.prop" || die 'LineageOS23.2 required'
    grep -q '^ro.build.version.sdk=36$' "$SYSTEM/build.prop" || die 'Android API36 required'
}
scratch_kib() {
    sc_required=$((FULL_PAYLOAD_KIB + 32768))
    if [ "$UPGRADING" = 1 ]; then
        while read -r sc_hash sc_rel; do
            sc_bytes=$(stat -c %s "$(target "$sc_rel")") || return 1
            sc_required=$((sc_required + (sc_bytes + 1023) / 1024))
        done < "$INSTALLED_MANIFEST"
        if [ "$HAS_POLICY" = 1 ]; then
            while read -r sc_post sc_pre sc_rel; do
                manifest_hash "$INSTALLED_MANIFEST" "$sc_rel" >/dev/null && continue
                sc_bytes=$(stat -c %s "$(target "$sc_rel")") || return 1
                sc_required=$((sc_required + (sc_bytes + 1023) / 1024))
            done < "$WORK/policy/patches.tsv"
        fi
    fi
    printf '%s\n' "$sc_required"
}
free_kib() {
    blocks=$(stat -f -c %a "$1") || return 1
    block_size=$(stat -f -c %S "$1") || return 1
    case "$blocks:$block_size" in *[!0-9:]*) return 1 ;; esac
    [ -n "$blocks" ] && [ -n "$block_size" ] && [ "$block_size" -ge 1024 ] || return 1
    [ "$((block_size % 1024))" = 0 ] || return 1
    # Recovery mksh uses signed 32-bit arithmetic. Convert the block unit first.
    printf '%s\n' "$((blocks * (block_size / 1024)))"
}
prepare_parent() {
    mkdir -p "${1%/*}" || return 1
    parent="${1%/*}"
    while :; do
        owned_parent=0
        case "$parent" in
            "$(app_root)"|"$(app_root)/"*|"$PRODUCT/etc/realme-oem-addons"|"$PRODUCT/etc/realme-oem-addons/camera-policy"|"$PRODUCT/etc/realme-oem-addons/camera-policy/"*) owned_parent=1 ;;
        esac
        if [ "$HAS_ASSETS" = 1 ]; then
            case "$parent" in "$(asset_root)") owned_parent=1 ;; esac
        fi
        for pp_oldroot in $(legacy_app_roots); do
            case "$parent" in "$pp_oldroot"|"$pp_oldroot/"*) owned_parent=1 ;; esac
        done
        [ "$owned_parent" = 1 ] || break
        chown 0:0 "$parent" && chmod 0755 "$parent" && \
            chcon u:object_r:system_file:s0 "$parent" || return 1
        parent="${parent%/*}"
    done
}
prune() {
    if [ "$HAS_ASSETS" = 1 ]; then rmdir "$(asset_root)" 2>/dev/null || true; fi
    if [ "$HAS_POLICY" = 1 ]; then
        rmdir "$PRODUCT/etc/realme-oem-addons/camera-policy/preimages" \
            "$PRODUCT/etc/realme-oem-addons/camera-policy/postimages" \
            "$PRODUCT/etc/realme-oem-addons/camera-policy" 2>/dev/null || true
    fi
    rmdir "$(app_root)/lib/arm64" "$(app_root)/lib" \
        "$(app_root)" "$PRODUCT/etc/realme-oem-addons" 2>/dev/null || true
    for pr_oldroot in $(legacy_app_roots); do
        rmdir "$pr_oldroot/lib/arm64" "$pr_oldroot/lib" \
            "$pr_oldroot" 2>/dev/null || true
    done
}
apply_files() {
    while read -r hash rel; do
        dest=$(target "$rel")
        if policy_patch "$rel"; then
            if [ "$ACTION" = remove ]; then
                if retain_shared "$rel"; then continue; fi
                desired="$WORK/policy/preimages/${rel##*/}"
                desired_hash=$(policy_before "$rel")
            else
                desired="$WORK/policy/postimages/${rel##*/}"
                desired_hash="$hash"
            fi
            actual=$(sha256sum "$dest") || die 'Policy target disappeared'
            if [ "${actual%% *}" = "$desired_hash" ]; then continue; fi
            printf '%s\n' "$rel" >> "$WORK/journal"
            cp "$desired" "$dest.realme-new" || die 'Cannot stage policy file'
            chown 0:0 "$dest.realme-new" && chmod 0644 "$dest.realme-new" || die 'Cannot set policy metadata'
            chcon "$(file_context "$rel")" "$dest.realme-new" || die 'Cannot label policy file'
            mv "$dest.realme-new" "$dest" || die 'Cannot commit policy file'
            continue
        fi
        if [ "$ACTION" = remove ]; then
            if retain_shared "$rel"; then continue; fi
            [ -f "$dest" ] || continue
            printf '%s\n' "$rel" >> "$WORK/journal"
            rm "$dest" || die "Cannot remove $rel"
        else
            if [ -f "$dest" ]; then
                existing=$(sha256sum "$dest")
                [ "${existing%% *}" != "$hash" ] || continue
                [ "$UPGRADING" = 1 ] || die 'Unexpected existing file during install'
            fi
            prepare_parent "$dest" || die 'Cannot prepare app directory'
            printf '%s\n' "$rel" >> "$WORK/journal"
            cp "$WORK/payload/$rel" "$dest.realme-new" || die 'Copy failed'
            chown 0:0 "$dest.realme-new"
            chmod 0644 "$dest.realme-new"
            case "$rel" in system/addon.d/*) chmod 0755 "$dest.realme-new" ;; esac
            chcon "$(file_context "$rel")" "$dest.realme-new" || die 'Cannot label staged file'
            mv "$dest.realme-new" "$dest" || die 'Atomic file commit failed'
        fi
    done < "$WORK/manifest.sha256"
}
verify_persisted_files() {
    # A cache-time hash and sync did not establish durable content in the first
    # Gallery trial. End writable mounts, then verify through fresh RO mounts.
    sync || die 'Cannot sync before durable verification'
    DURABILITY_PENDING=1
    # Once either writable mount is released, an automatic rollback must not
    # copy into its now-unmounted scratch directory. Retain complete staging
    # on any failure for a separately reviewed normal-filesystem recovery.
    WRITABLE=0
    umount "$PRODUCT" || die 'Cannot release product for durable verification'
    MOUNTED_PRODUCT=0
    umount "$SYSTEM_MOUNT" || die 'Cannot release system for durable verification'
    MOUNTED_SYSTEM=0
    mount -t ext4 -o ro,noload "/dev/block/mapper/system$SLOT" "$SYSTEM_MOUNT" || die 'Cannot verify persisted system'
    MOUNTED_SYSTEM=1
    mount -t ext4 -o ro,noload "/dev/block/mapper/product$SLOT" "$PRODUCT" || die 'Cannot verify persisted product'
    MOUNTED_PRODUCT=1
    while read -r durable_hash durable_rel; do
        durable_dest=$(target "$durable_rel")
        if [ "$ACTION" = remove ]; then
            if policy_patch "$durable_rel"; then
                if ! retain_shared "$durable_rel"; then
                    durable_hash=$(policy_before "$durable_rel") || die 'Missing restored policy identity'
                fi
            elif ! retain_shared "$durable_rel"; then
                [ ! -e "$durable_dest" ] && [ ! -L "$durable_dest" ] || die "Removed file persisted: $durable_rel"
                continue
            fi
        fi
        durable_actual=$(sha256sum "$durable_dest") || die "Cannot read persisted file: $durable_rel"
        [ "${durable_actual%% *}" = "$durable_hash" ] || die "Durable file hash mismatch: $durable_rel"
    done < "$WORK/manifest.sha256"
    DURABILITY_PENDING=0
}
cleanup() {
    rc=$?
    set +e
    rollback_failed=0
    trap - EXIT HUP INT TERM
    if [ "$WRITABLE" = 1 ] && [ "$COMMITTED" = 0 ] && [ -f "$WORK/journal" ]; then
        ui 'Rolling back the interrupted file transaction'
        while IFS= read -r rel; do
            allowed "$rel" || continue
            dest=$(target "$rel") || continue
            if [ "$ACTION" = remove ] || [ "$UPGRADING" = 1 ]; then
                if [ -f "$WORK/rollback/$rel" ]; then
                    if prepare_parent "$dest" && cp -p "$WORK/rollback/$rel" "$dest" && \
                        chcon "$(file_context "$rel")" "$dest"; then :
                    else rollback_failed=1; rc=1; fi
                elif [ "$UPGRADING" = 1 ]; then
                    rm -f "$dest" || { rollback_failed=1; rc=1; }
                fi
                rm -f "$dest.realme-new"
            else
                if policy_patch "$rel"; then
                    if cp "$WORK/policy/preimages/${rel##*/}" "$dest" &&
                        chown 0:0 "$dest" && chmod 0644 "$dest" &&
                        chcon "$(file_context "$rel")" "$dest"; then :
                    else rollback_failed=1; rc=1; fi
                    rm -f "$dest.realme-new"
                else
                    rm -f "$dest" "$dest.realme-new" || true
                fi
            fi
        done < "$WORK/journal"
        prune
    fi
    if [ "$WRITABLE" = 1 ]; then sync || rc=1; fi
    release_policy_inputs || rc=1
    if [ "$MOUNTED_PRODUCT" = 1 ]; then
        if umount "$PRODUCT"; then MOUNTED_PRODUCT=0; else rc=1; fi
    fi
    if [ "$MOUNTED_SYSTEM" = 1 ]; then
        if umount "$SYSTEM_MOUNT"; then MOUNTED_SYSTEM=0; else rc=1; fi
    fi
    # Never recurse into scratch while a target filesystem remains mounted below it.
    if [ "$MOUNTED_PRODUCT" = 0 ] && [ "$MOUNTED_SYSTEM" = 0 ] && [ -z "$MOUNTED_EXTRAS" ] && [ "$rollback_failed" = 0 ] && [ "$DURABILITY_PENDING" = 0 ]; then
        case "$WORK" in /tmp/realme-oem-*) rm -rf "$WORK" ;; esac
    else
        ui "Cleanup, rollback or durable verification incomplete; scratch retained at $WORK. Stop and collect recovery logs."
    fi
    exit "$rc"
}
ui "$TITLE: $ACTION (experimental optional add-on)"
[ "$(getprop ro.bootmode)" = recovery ] || die 'Run from recovery, not Android'
[ "$(getprop ro.product.model)" = RMX3301 ] || die 'RMX3301 required'
[ "$(getprop ro.bionic.arch)" = arm64 ] || die 'ARM64 recovery required'
SLOT=$(getprop ro.boot.slot_suffix)
case "$SLOT" in _a|_b) ;; *) die 'Unrecognized recovery boot slot' ;; esac
for tool in unzip sha256sum mount umount readlink stat chcon chown chmod mktemp cp mv sync od grep tr cat rm rmdir find; do
    command -v "$tool" >/dev/null 2>&1 || die "Missing recovery command: $tool"
done
WORK=$(mktemp -d /tmp/realme-oem-XXXXXX)
SYSTEM_MOUNT="$WORK/system-mount"
PRODUCT="$WORK/product-mount"
mkdir "$SYSTEM_MOUNT" "$PRODUCT"
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
# Read the existing VAB message. Never modify misc/BCB or snapshot state.
set -- $(od -An -tu1 -j32768 -N7 /dev/block/by-name/misc)
[ "$#" = 7 ] && [ "$1" = 2 ] && [ "$2" = 176 ] && [ "$3" = 10 ] && \
    [ "$4" = 116 ] && [ "$5" = 86 ] && [ "$6" = 0 ] || die 'Unknown or active VAB merge state; boot Android to finish its OTA before installing add-ons'
case "$7" in 0|1) ;; *) die 'Invalid VAB source slot' ;; esac
if command -v snapshotctl >/dev/null 2>&1; then
    snapshotctl dump > "$WORK/snapshot.txt" || die 'Cannot inspect snapshot state'
    grep -q '^Update state: none$' "$WORK/snapshot.txt" || die 'An OTA snapshot operation is pending'
fi
if [ -n "$HELPER_SHA" ]; then
    unzip -p "$ZIP" recovery/libraries.sha256 > "$WORK/recovery-libraries.sha256" || die 'Missing recovery API bindings'
    actual=$(sha256sum "$WORK/recovery-libraries.sha256")
    [ "${actual%% *}" = "$HELPER_LIBRARIES_SHA" ] || die 'Recovery API binding manifest differs'
    sha256sum -c "$WORK/recovery-libraries.sha256" >/dev/null || die 'Recovery API differs; use matching project recovery'
    unzip -p "$ZIP" recovery/prepare-logical > "$WORK/prepare-logical" || die 'Missing logical preparation helper'
    actual=$(sha256sum "$WORK/prepare-logical")
    [ "${actual%% *}" = "$HELPER_SHA" ] || die 'Logical preparation helper differs'
    chmod 0700 "$WORK/prepare-logical"
    "$WORK/prepare-logical" --prepare-missing || die 'Standard logical preparation refused; stop and collect recovery logs'
fi
for part in system product; do
    dev="/dev/block/mapper/${part}${SLOT}"
    verify_logical_identity "$part"
    if [ "$part" = system ]; then
        mount -t ext4 -o ro,noload "$dev" "$SYSTEM_MOUNT" || die 'Cannot read-mount system'
        MOUNTED_SYSTEM=1
    else
        mount -t ext4 -o ro,noload "$dev" "$PRODUCT" || die 'Cannot read-mount product'
        MOUNTED_PRODUCT=1
    fi
done
SYSTEM="$SYSTEM_MOUNT/system"
verify_rom
verify_platform_framework
if [ "$ISOLATED_PHOTOS" = 1 ] && [ "$ACTION" != remove ]; then
    [ "$PROFILE" = photos ] || die 'Invalid isolated Photos profile'
    for root in "$SYSTEM" "$PRODUCT"; do
        for rel in app/RealmeStockCamera priv-app/RealmeStockCamera framework/realme-camera-unit-sdk.jar etc/permissions/realme-camera-unit-sdk.xml; do
            [ ! -e "$root/$rel" ] && [ ! -L "$root/$rel" ] || die 'Gallery alpha cannot yet coexist with Camera/shared SDK; no files changed'
        done
    done
fi
if [ "$APP_PARTITION" = system ]; then
    [ "$PROFILE" = camera ] || die 'System placement restricted to Camera'
    [ ! -e "$PRODUCT/app/$APPDIR" ] && [ ! -L "$PRODUCT/app/$APPDIR" ] || die 'Remove legacy product Camera before core update'
    if [ "$HAS_PEERS" = 0 ]; then
        [ ! -e "$PRODUCT/app/$OTHER_APPDIR" ] && [ ! -e "$SYSTEM/app/$OTHER_APPDIR" ] || die 'Unreviewed Photos combination'
    fi
    [ -d "$SYSTEM/app" ] && [ -d "$SYSTEM/framework" ] && [ -d "$SYSTEM/etc/permissions" ] || die 'System app/library directories missing'
fi
[ -d "$PRODUCT/app" ] && [ -d "$PRODUCT/etc/sysconfig" ] && [ -d "$SYSTEM/addon.d" ] || die 'Expected add-on directories missing'
[ -d "$PRODUCT/framework" ] && [ -d "$PRODUCT/etc/permissions" ] || die 'Expected library directories missing'
trust=$(unzip -p "$SYSTEM/etc/security/otacerts.zip" releasekey.x509.pem | tr -d '[:space:]' | sha256sum)
trust="${trust%% *}"
[ "$trust" = "$CERT_SHA" ] || die 'Installed ROM signing certificate mismatch'
ui "Verified target slot $SLOT, Lineage23.2 and project signing certificate"
select_shared_retention
unzip -p "$ZIP" manifest.sha256 > "$WORK/manifest.sha256"
[ -s "$WORK/manifest.sha256" ] || die 'Empty file manifest'
# Validate manifest syntax before using any path for policy-support inspection.
while read -r hash rel extra; do
    [ -z "${extra:-}" ] && allowed "$rel" || die 'Unexpected manifest path'
    [ "${#hash}" = 64 ] || die 'Invalid manifest digest'
    case "$hash" in *[!0-9a-f]*) die 'Invalid manifest digest' ;; esac
done < "$WORK/manifest.sha256"
prepare_policy
select_peer
select_upgrade
verify_legacy_sdk_state
verify_existing_files
verify_owned_tree
verify_policy_inputs
select_space_budget
if [ "$ACTION" != remove ]; then
    free_product=$(free_kib "$PRODUCT") || die 'Cannot measure product capacity'
    free_system=$(free_kib "$SYSTEM_MOUNT") || die 'Cannot measure system capacity'
    [ "$free_product" -ge "$PRODUCT_KIB" ] || die 'Insufficient product space; nothing resized'
    [ "$free_system" -ge "$SYSTEM_KIB" ] || die 'Insufficient system space'
fi
free_tmp=$(free_kib "$WORK") || die 'Cannot measure recovery capacity'
# Payload extraction and complete predecessor rollback still need full staging.
# The per-partition delta budget must never lower this RAM requirement.
scratch_required=$(scratch_kib) || die 'Cannot size full payload and rollback staging'
ui "Recovery scratch: $free_tmp KiB available, $scratch_required KiB required"
[ "$free_tmp" -ge "$scratch_required" ] || die 'Insufficient recovery scratch space'
if [ "$ACTION" = check ]; then
    ui 'Read-only preflight passed; application runtime support remains unproven'
    COMMITTED=1
    exit 0
fi
if [ "$ACTION" = install ] && [ "$ALREADY_INSTALLED" = 1 ]; then
    ui 'Exact installed Camera and policy verified; no writable mount needed'
    COMMITTED=1
    exit 0
fi
case "$ACTION" in install|remove) ;; *) die 'Unknown operation' ;; esac
if [ "$ACTION" = install ]; then
    unzip -o "$ZIP" 'payload/*' -d "$WORK" >/dev/null || die 'Payload extraction failed'
    (cd "$WORK/payload" && sha256sum -c "$WORK/manifest.sha256") || die 'Payload hash mismatch'
    stage_upgrade_rollback
else
    stage_remove_rollback
fi
# End no-journal read mounts before normal writable mounts.
umount "$PRODUCT" || die 'Cannot release product read mount'
MOUNTED_PRODUCT=0
umount "$SYSTEM_MOUNT" || die 'Cannot release system read mount'
MOUNTED_SYSTEM=0
# Never change mapper protection or raw-write around a mount failure.
mount -t ext4 -o rw "/dev/block/mapper/system$SLOT" "$SYSTEM_MOUNT" || die 'Normal system write mount refused'
MOUNTED_SYSTEM=1
mount -t ext4 -o rw "/dev/block/mapper/product$SLOT" "$PRODUCT" || die 'Normal product write mount refused'
MOUNTED_PRODUCT=1
WRITABLE=1
: > "$WORK/journal"
if [ "$ACTION" = install ]; then remove_upgrade_stale; fi
apply_files
if [ "$ACTION" = install ]; then
    while read -r hash rel; do
        actual=$(sha256sum "$(target "$rel")")
        actual="${actual%% *}"
        [ "$actual" = "$hash" ] || die "Installed hash mismatch: $rel"
    done < "$WORK/manifest.sha256"
    if [ "$UPGRADING" = 1 ]; then
        while read -r sv_hash sv_rel; do
            if ! manifest_hash "$WORK/manifest.sha256" "$sv_rel" >/dev/null; then
                [ ! -e "$(target "$sv_rel")" ] || die 'Stale dependency remained after upgrade'
            fi
        done < "$INSTALLED_MANIFEST"
        prune
    fi
else
    if [ "$HAS_POLICY" = 1 ]; then
        while read -r post pre rel; do
            if retain_shared "$rel"; then pre="$post"; fi
            actual=$(sha256sum "$(target "$rel")") || die 'Restored policy file missing'
            [ "${actual%% *}" = "$pre" ] || die 'Restored policy hash mismatch'
        done < "$WORK/policy/patches.tsv"
    fi
    prune
fi
verify_persisted_files
COMMITTED=1
ui "Completed $ACTION on $SLOT. Existing app data and Lineage applications are preserved."
ui 'Reboot Android to validate. This does not establish successful app operation or OTA retention.'
