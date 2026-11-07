package com.oplus.aiunit.vision;

import android.os.Build;
import java.util.Objects;

/** Version-bound ActionReceiverProperties ABI for the optional Gallery APK. */
public final class ft {
    public final String a;
    public final String b;
    public final int c;

    public ft(String action, String permission, int flags) {
        a = Objects.requireNonNull(action, "action");
        b = permission;
        // Android 14 enforces an explicit state. Retain older-Android behavior,
        // every explicit state, and instant-app visibility rather than guessing.
        // A sender permission remains enforced when the receiver is exported.
        // Unspecified, unpermissioned app-bus receivers default to private.
        c = Build.VERSION.SDK_INT >= 34 && (flags & 7) == 0
                ? flags | (permission == null || permission.isEmpty() ? 4 : 2)
                : flags;
    }

    @Override public boolean equals(Object other) {
        if (this == other) return true;
        if (!(other instanceof ft)) return false;
        ft value = (ft) other;
        return a.equals(value.a) && Objects.equals(b, value.b) && c == value.c;
    }

    @Override public int hashCode() {
        return (a.hashCode() * 31 + (b == null ? 0 : b.hashCode())) * 31 + c;
    }

    @Override public String toString() {
        return "ActionReceiverProperties(action=" + a + ", permission=" + b
                + ", flags=" + c + ")";
    }
}
