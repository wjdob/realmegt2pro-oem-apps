package realme.clock.compat;

import android.content.Context;
import com.oplus.alarmclock.AlarmClockApplication;
import java.lang.reflect.Method;

/** Optional OEM features retain their native query when accessible to this app. */
public final class FeatureConfig {
    private static final FeatureConfig INSTANCE = new FeatureConfig();
    private final Object delegate;
    private final Method query;
    private final Method ipcQuery;

    private FeatureConfig() {
        Object object = null;
        Method normal = null, ipc = null;
        try {
            Class<?> type = Class.forName("com.oplus.content.OplusFeatureConfigManager");
            object = type.getMethod("getInstance").invoke(null);
            normal = type.getMethod("hasFeature", String.class);
            try { ipc = type.getMethod("hasFeatureIPC", String.class); }
            catch (ReflectiveOperationException unavailable) { ipc = normal; }
        } catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) {
            object = null;
            normal = ipc = null;
        }
        delegate = object;
        query = normal;
        ipcQuery = ipc;
    }

    public static FeatureConfig getInstance() { return INSTANCE; }
    public boolean hasFeature(String name) { return query(name, query); }
    public boolean hasFeatureIPC(String name) { return query(name, ipcQuery); }

    private boolean query(String name, Method method) {
        if (name == null || name.isEmpty()) return false;
        if (delegate != null && method != null) {
            try { return Boolean.TRUE.equals(method.invoke(delegate, name)); }
            catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) { }
        }
        // The same public PackageManager fallback already exists in OEM Ll3/a.
        Context context = AlarmClockApplication.f();
        return context != null && context.getPackageManager().hasSystemFeature(name);
    }
}
