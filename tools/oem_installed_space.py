"""Explicit API37 formats-bundle extension; preserve prior installer recipes."""
import hashlib
import oem_platform

BASE_SHA256='36b39339a663fc09ed7e48410c4204455ffdc71d0d10fe34b8d67feebb0099cf'
FUNCTION='''select_space_budget() {
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
'''


def prepare(source,profile):
    if 'installed_space_budget' not in profile:return source
    if (profile['installed_space_budget'] is not True or
            oem_platform.checked(profile)!=oem_platform.ANDROID17 or
            profile.get('id') not in ('camera','photos') or
            hashlib.sha256(source).hexdigest()!=BASE_SHA256):
        raise ValueError('unreviewed installed-space extension')
    text=source.decode()
    anchor='stage_upgrade_rollback() {';entry='verify_policy_inputs\nif [ "$ACTION" != remove ]; then'
    if text.count(anchor)!=1 or text.count(entry)!=1:raise ValueError('space extension preimage differs')
    return text.replace(anchor,FUNCTION+anchor).replace(entry,'verify_policy_inputs\nselect_space_budget\nif [ "$ACTION" != remove ]; then').encode()
