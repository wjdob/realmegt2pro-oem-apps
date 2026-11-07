package realme.gallery.compat;

/** Resolve the existing app coroutine through its public interfaces, without a Kotlin dependency. */
public final class MediaSaveScope {
    public static boolean active(Object scope) {
        if (scope == null || Thread.currentThread().isInterrupted()) return false;
        try {
            ClassLoader loader = MediaSaveScope.class.getClassLoader();
            Class<?> scopeType = Class.forName("kotlinx.coroutines.CoroutineScope", false, loader);
            Class<?> contextType = Class.forName("kotlin.coroutines.CoroutineContext", false, loader);
            Class<?> keyType = Class.forName("kotlin.coroutines.CoroutineContext$Key", false, loader);
            Class<?> jobType = Class.forName("kotlinx.coroutines.Job", false, loader);
            Object context = scopeType.getMethod("getCoroutineContext").invoke(scope);
            Object key = jobType.getField("Key").get(null);
            Object job = contextType.getMethod("get", keyType).invoke(context, key);
            // An unbound scope must not authorize an operation after its editor was cancelled.
            return jobType.isInstance(job) && Boolean.TRUE.equals(jobType.getMethod("isActive").invoke(job));
        } catch (ReflectiveOperationException | LinkageError | RuntimeException unavailable) {
            return false;
        }
    }
}
