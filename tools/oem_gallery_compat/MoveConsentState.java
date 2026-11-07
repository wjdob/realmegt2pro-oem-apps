package realme.gallery.compat;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

/** One bounded consent attempt; a late or repeated callback cannot restart it. */
public final class MoveConsentState {
    private final CountDownLatch signal = new CountDownLatch(1);
    private final LongSupplier clock;
    private final long deadline;
    private boolean finished;
    private boolean approved;

    public MoveConsentState(LongSupplier clock, long timeoutMillis) {
        if (timeoutMillis <= 0 || timeoutMillis > 300000) {
            throw new IllegalArgumentException("invalid consent deadline");
        }
        this.clock = clock;
        this.deadline = Math.addExact(clock.getAsLong(), timeoutMillis);
    }

    public synchronized boolean finish(boolean approval) {
        if (finished) return false;
        approved = approval && clock.getAsLong() < deadline;
        finished = true;
        signal.countDown();
        return true;
    }

    public synchronized boolean pending() {
        if (!finished && clock.getAsLong() >= deadline) finish(false);
        return !finished;
    }

    public boolean await() throws InterruptedException {
        try {
            final long remaining;
            synchronized (this) { remaining = deadline - clock.getAsLong(); }
            if (remaining <= 0 || !signal.await(remaining, TimeUnit.MILLISECONDS)) finish(false);
            synchronized (this) { return finished && approved; }
        } catch (InterruptedException interrupted) {
            finish(false);
            throw interrupted;
        }
    }
}
