package realme.gallery.compat;

import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;

/** Optional vendor scheduling hints must not prevent ordinary drawing. */
public final class OptionalOemPerformance {
    private static final boolean AVAILABLE = available(OptionalOemPerformance.class.getClassLoader());

    public static boolean available() {
        return AVAILABLE;
    }

    public static boolean available(ClassLoader loader) {
        try {
            Class<?> client = Class.forName("com.oplus.uah.UAHResClient", false, loader);
            Class<?> event = Class.forName("com.oplus.uah.info.UAHEventRequest", false, loader);
            Method get = client.getMethod("get", Class.class);
            Method release = client.getMethod("release", int.class);
            Method acquire = client.getMethod("acquireEvent", event);
            event.getConstructor(int.class, String.class, int.class, ArrayList.class);
            return Modifier.isPublic(client.getModifiers()) && !client.isInterface()
                    && Modifier.isPublic(event.getModifiers()) && !Modifier.isAbstract(event.getModifiers())
                    && Modifier.isStatic(get.getModifiers()) && get.getReturnType() == client
                    && !Modifier.isStatic(release.getModifiers()) && release.getReturnType() == void.class
                    && !Modifier.isStatic(acquire.getModifiers()) && acquire.getReturnType() == int.class;
        } catch (ReflectiveOperationException | LinkageError unavailable) {
            return false;
        }
    }
}
