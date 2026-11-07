package realme.gallery.compat;

import android.os.Build;

/** Version-bound native provider supplied by the signed optional recovery bundle. */
public final class RemoverNativeCompat {
    public static String packagedLibrary(String requested) {
        if (Build.VERSION.SDK_INT < 36 || !"RMX3301".equals(Build.MODEL)
                || requested == null || requested.indexOf("..") >= 0) return null;
        String tail = "/com.coloros.gallery3d/files/component/EliminatePenSourceV2/libcv_inpainting.so";
        if (!requested.endsWith(tail)) return null;
        String prefix = requested.substring(0, requested.length() - tail.length());
        if (!"/data/data".equals(prefix) && !prefix.matches("/data/user/[0-9]+")) return null;
        return "realme_cv_inpainting_v10";
    }

    public static void load(String requested) {
        String packaged = packagedLibrary(requested);
        if (packaged == null) System.load(requested);
        else System.loadLibrary(packaged);
    }
}
