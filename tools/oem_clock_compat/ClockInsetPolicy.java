package realme.clock.compat;

/** Reserve only the part of the navigation inset overlapping the Clock tabs. */
public final class ClockInsetPolicy {
    private ClockInsetPolicy() { }
    public static int bottomPadding(int baseline, int current, int labelBottom,
                                    int windowBottom, int navigationInset) {
        int inset = Math.max(0, navigationInset);
        int overlap = labelBottom + (current - baseline) - (windowBottom - inset);
        return baseline + Math.min(inset, Math.max(0, overlap));
    }
}
