package realme.gallery.compat;

/** Keep OEM haptics when their complete API exists; otherwise use Toolkit's fallback. */
public final class OptionalOemHaptics {
    public static boolean available() {
        return available(OptionalOemHaptics.class.getClassLoader());
    }

    public static boolean available(ClassLoader loader) {
        try {
            Class.forName("com.oplus.os.LinearmotorVibrator", false, loader);
            Class.forName("com.oplus.os.WaveformEffect", false, loader);
            Class.forName("com.oplus.os.WaveformEffect$Builder", false, loader);
            return true;
        } catch (ClassNotFoundException | LinkageError absent) {
            return false;
        }
    }
}
