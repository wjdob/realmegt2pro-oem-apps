package realme.clock.compat;

import android.content.Context;
import android.os.Build;

/** Query OEM version/device names when available; never invent an OEM version. */
public final class OemBuild {
    private OemBuild() { }
    public static int getOplusOSVERSION() {
        try {
            return ((Number) Class.forName("com.oplus.os.OplusBuild")
                    .getMethod("getOplusOSVERSION").invoke(null)).intValue();
        } catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) {
            return 0;
        }
    }
    public static String getDeviceName() { return name(null, false); }
    public static String getDeviceName(Context context) { return name(context, true); }
    private static String name(Context context, boolean withContext) {
        try {
            Class<?> type = Class.forName("com.oplus.os.OplusBuild");
            Object value = withContext ? type.getMethod("getDeviceName", Context.class).invoke(null, context)
                    : type.getMethod("getDeviceName").invoke(null);
            if (value instanceof String && !((String) value).isEmpty()) return (String) value;
        } catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) { }
        return Build.MODEL;
    }
    private static Object field(String name) {
        try { return Class.forName("com.oplus.os.OplusBuild$VERSION").getField(name).get(null); }
        catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) { return null; }
    }
    private static int number(String name) {
        Object value = field(name);
        return value instanceof Number ? ((Number) value).intValue() : 0;
    }
    public static final class VERSION {
        public static final int SDK_VERSION = number("SDK_VERSION");
        public static final int SDK_SUB_VERSION = number("SDK_SUB_VERSION");
        public static final String RELEASE;
        static {
            Object value = field("RELEASE");
            RELEASE = value instanceof String ? (String) value : "";
        }
        private VERSION() { }
    }
}
