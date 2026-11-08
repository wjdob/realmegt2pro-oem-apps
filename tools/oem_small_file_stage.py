"""Opt-in API37 staging for small app files before SELinux metadata updates."""
import hashlib
from pathlib import Path
import oem_installed_space
import oem_platform

FUNCTION='''stage_payload() {
    staged_size=$(stat -c %s "$1") || return 1
    case "$staged_size" in ''|*[!0-9]*) return 1 ;; esac
    if [ "$staged_size" -le 4096 ]; then
        dd if=/dev/zero of="$2" bs=4096 count=1 || return 1
        dd if="$1" of="$2" conv=notrunc || return 1
    else
        cp "$1" "$2" || return 1
    fi
}
finish_staged_payload() {
    if [ "$staged_size" -le 4096 ]; then
        truncate -s "$staged_size" "$1" || return 1
    fi
    staged_hash=$(sha256sum "$1") || return 1
    [ "${staged_hash%% *}" = "$2" ]
}
'''


def prepare(source,profile):
    if 'extent_staged_metadata' not in profile:return source
    base=(Path(__file__).resolve().parent/'oem_addons/update-binary.sh').read_bytes()
    if (profile['extent_staged_metadata'] is not True or
            oem_platform.checked(profile)!=oem_platform.ANDROID17 or
            profile.get('id') not in ('camera','photos') or
            hashlib.sha256(base).hexdigest()!=oem_installed_space.BASE_SHA256 or
            source!=oem_installed_space.prepare(base,profile)):
        raise ValueError('unreviewed small-file staging extension')
    text=source.decode()
    changes={
        'apply_files() {':FUNCTION+'apply_files() {',
        '            cp "$WORK/payload/$rel" "$dest.realme-new" || die \'Copy failed\'':
        '            stage_payload "$WORK/payload/$rel" "$dest.realme-new" || die \'Copy failed\'',
        '            mv "$dest.realme-new" "$dest" || die \'Atomic file commit failed\'':
        '            finish_staged_payload "$dest.realme-new" "$hash" || die \'Staged file hash differs\'\n            mv "$dest.realme-new" "$dest" || die \'Atomic file commit failed\'',
        'for tool in unzip sha256sum mount umount readlink stat chcon chown chmod mktemp cp mv sync od grep tr cat rm rmdir find; do':
        'for tool in unzip sha256sum mount umount readlink stat chcon chown chmod mktemp cp mv sync od grep tr cat rm rmdir find dd truncate; do',
    }
    for before,after in changes.items():
        if text.count(before)!=1:raise ValueError('small-file staging preimage differs')
        text=text.replace(before,after)
    return text.encode()
