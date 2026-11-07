package realme.camera.compat;

import android.app.Application;
import android.hardware.camera2.CaptureRequest;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/** Reproduce stock's real-caller session declaration without changing cameraserver. */
public final class SessionCaller {
    private SessionCaller() {}

    public static void apply(CaptureRequest.Builder builder) {
        final Application app;
        try {
            app = (Application) Class.forName("android.app.ActivityThread")
                    .getMethod("currentApplication").invoke(null);
        } catch (ReflectiveOperationException error) {
            throw new IllegalStateException("Camera caller context unavailable", error);
        }
        if (app == null || !"com.oplus.camera".equals(app.getPackageName())) {
            throw new IllegalStateException("Camera SDK used outside its owning application");
        }
        // Stock Camera3Device uses CameraMetadata.update(tag, String8(packageName)).
        // That overload stores a terminating zero byte. Use the HAL's existing key.
        byte[] caller = (app.getPackageName() + "\0").getBytes(StandardCharsets.UTF_8);
        CaptureRequest.Key<byte[]> key =
                new CaptureRequest.Key<>("com.oplus.packageName", byte[].class);
        builder.set(key, caller);
        if (!Arrays.equals(caller, builder.get(key))) {
            throw new IllegalStateException("Camera caller metadata readback differs");
        }
    }
}
