package realme.clock.compat;

/** Optional OEM visibility/identifier APIs; Android notification checks stay intact. */
public final class OemNotifications {
    private final Object delegate;
    public OemNotifications() {
        Object result = null;
        try { result = Class.forName("android.app.OplusNotificationManager").getConstructor().newInstance(); }
        catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) { }
        delegate = result;
    }
    public int getAppVisibility(String name, int user) {
        if (delegate != null) {
            try { return ((Number) delegate.getClass().getMethod("getAppVisibility", String.class, int.class)
                    .invoke(delegate, name, user)).intValue(); }
            catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) { }
        }
        // OEM AlarmStateManager already treats an unavailable OEM manager as allowed.
        // This is an OEM restriction check, not a notification permission grant.
        return 0;
    }
    public String getOpenid(String name, int user, String type) {
        if (delegate != null) {
            try { return (String) delegate.getClass().getMethod("getOpenid", String.class, int.class, String.class)
                    .invoke(delegate, name, user, type); }
            catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) { }
        }
        return null;
    }
    public void clearOpenid(String name, int user, String type) {
        if (delegate != null) {
            try { delegate.getClass().getMethod("clearOpenid", String.class, int.class, String.class)
                    .invoke(delegate, name, user, type); }
            catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) { }
        }
    }
}
