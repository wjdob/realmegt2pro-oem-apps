package realme.gallery.compat;

/** Shared state for a media-access offer and the subsequent platform write request. */
public final class MediaConsentFlow {
    public enum Step { NEW, OFFER, SETTINGS, WRITE, FINISHED }
    private Step step = Step.NEW;

    public synchronized Step step() { return step; }

    public synchronized Step begin(boolean management, boolean explained) {
        if (step == Step.NEW) step = management || explained ? Step.WRITE : Step.OFFER;
        return step;
    }

    public synchronized boolean settings() {
        if (step != Step.OFFER) return false;
        step = Step.SETTINGS;
        return true;
    }

    public synchronized boolean justThisChange() {
        if (step != Step.OFFER) return false;
        step = Step.WRITE;
        return true;
    }

    public synchronized boolean settingsReturned(boolean management) {
        if (step != Step.SETTINGS) return false;
        step = management ? Step.WRITE : Step.FINISHED;
        return management;
    }

    public synchronized boolean writeReturned() {
        if (step != Step.WRITE) return false;
        step = Step.FINISHED;
        return true;
    }

    public synchronized void cancel() { step = Step.FINISHED; }
}
