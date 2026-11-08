"""Check the native menu ABI used by the keyed Lens adapter in the pinned APK."""
import argparse
import io
import json
from pathlib import Path
import zipfile

from oem_camera_window import abi, APK_SHA
from oem_sdk import sha

REQUIRED = {
    'Lcom/oplus/camera/ui/setting/menu/CameraSettingMenuPanel;': {
        'superclass': 'Landroid/widget/LinearLayout;',
        'methods': ['j(Ljava/lang/String;)Lrj/l;', 'v(Ljava/lang/String;ZZ)Z'], 'fields': []},
    'Lrj/l;': {'superclass': 'Ljava/lang/Object;',
        'methods': ['x()Ljava/lang/String;', 'q()Landroid/view/View;'],
        'fields': ['d:Z', 'j:Lrj/l$c;']},
    'Ltj/h;': {'superclass': 'Lrj/l;',
        'methods': ['onClick(Landroid/view/View;)V', 'onTouch(Landroid/view/View;Landroid/view/MotionEvent;)Z',
                    'q()Landroid/view/View;'], 'fields': []},
    'Lzj/y;': {'superclass': 'Lcom/airbnb/lottie/LottieAnimationView;',
        'methods': ['setMbClickUpdate(Z)V'], 'fields': []},
    'Lcom/oplus/camera/CameraManager$d;': {'superclass': 'Ljava/lang/Object;',
        'methods': ['J0(Ljava/lang/String;ZZ)Z'], 'fields': []},
}


def inspect_bytes(data):
    if sha(data) != APK_SHA:
        raise ValueError('unreviewed Camera APK for keyed Lens audit')
    found = {}; dexes = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if not name.endswith('.dex'): continue
            dex = archive.read(name); rows = abi(dex, set(REQUIRED))
            if set(rows) & set(found): raise ValueError('duplicate native Lens class')
            if rows: dexes[name] = sha(dex)
            found.update(rows)
    if set(found) != set(REQUIRED): raise ValueError('missing native Lens class')
    for name, required in REQUIRED.items():
        actual = found[name]
        if not actual['access'] & 1 or actual['superclass'] != required['superclass']:
            raise ValueError('native Lens class ABI differs: '+name)
        for kind in ('methods', 'fields'):
            for member in required[kind]:
                flags = actual[kind].get(member, 0)
                if not flags & 1 or flags & 8:
                    raise ValueError('native Lens instance member ABI differs: '+name+'->'+member)
    selected = {name: {'superclass': found[name]['superclass'], 'access': found[name]['access'],
                **{kind: {member: found[name][kind][member] for member in required[kind]}
                   for kind in ('methods', 'fields')}} for name, required in REQUIRED.items()}
    return {'apk_sha256': APK_SHA, 'dex_sha256': dexes, 'native_abi': selected,
            'scope': 'Exact public native class/member ABI. It does not establish live menu attachment or Lens launch.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_bytes(args.apk.read_bytes()), indent=2))
