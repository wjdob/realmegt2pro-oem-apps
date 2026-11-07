package realme.gallery.compat;

import android.os.Build;
import java.util.Map;

/** OEM model-service compatibility for the RMX3301 stock-derived runtime. */
public final class ModelRequestCompat {
    private ModelRequestCompat() {}

    public static void complete(Map<String, String> headers) {
        // Preserve a working OEM profile. This is a model-service request hint;
        // do not change Android's reported version or any system property.
        // V15.0.0 is pinned to this port's original .1410 vendor/app reference.
        if (headers != null && !headers.containsKey("oplusOSVersion")
                && "RMX3301".equals(Build.MODEL)) {
            headers.put("oplusOSVersion", "V15.0.0");
        }
    }
}
