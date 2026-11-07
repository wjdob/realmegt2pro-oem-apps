package realme.clock.compat;

/** OEM slider integration is optional; Android's existing lock-screen rules remain. */
public final class OptionalKeyguard {
    private OptionalKeyguard() { }
    public static void request(String command) {
        for (String name : new String[] {"android.view.OplusWindowManager", "android.view.OppoWindowManager"}) {
            try {
                Class<?> type = Class.forName(name);
                Object manager = type.getConstructor().newInstance();
                type.getMethod("requestKeyguard", String.class).invoke(manager, command);
                return;
            } catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) { }
        }
    }
}
