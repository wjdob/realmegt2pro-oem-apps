package realme.gallery.compat;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Application;
import android.app.Fragment;
import android.app.KeyguardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.MediaStore;
import android.provider.Settings;
import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.WeakHashMap;

/** Supported MediaStore requests shared by album moves and Replace original.
 * Management access suppresses platform confirmation; it never replaces a write grant.
 */
public final class MediaWriteConsent {
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final int SETTINGS_REQUEST = 19768;
    private static final int WRITE_REQUEST = 19769;
    private static final String PREFS = "realme.gallery.compat.media_access";
    private static final String EXPLAINED = "management_explained";
    private static final WeakHashMap<Fragment, Flow> FLOWS = new WeakHashMap<>();
    private static WeakReference<Activity> current = new WeakReference<>(null);
    private static Application registered;

    public interface Check { boolean ready(); }

    /** Called after the pinned Application's attachBaseContext, before its bootstrap. */
    public static synchronized void install(Application app) {
        if (Build.VERSION.SDK_INT < 31 || registered == app) return;
        if (registered != null || !"com.coloros.gallery3d".equals(app.getPackageName())) return;
        registered = app;
        app.registerActivityLifecycleCallbacks(new Application.ActivityLifecycleCallbacks() {
            public void onActivityResumed(Activity a) { current = new WeakReference<>(a); }
            public void onActivityPaused(Activity a) {
                if (current.get() == a) current = new WeakReference<>(null);
            }
            public void onActivityDestroyed(Activity a) {
                if (current.get() == a) current = new WeakReference<>(null);
            }
            public void onActivityCreated(Activity a, Bundle b) {}
            public void onActivityStarted(Activity a) {}
            public void onActivityStopped(Activity a) {}
            public void onActivitySaveInstanceState(Activity a, Bundle b) {}
        });
    }

    private static boolean usable(Activity activity) {
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) return false;
        KeyguardManager keyguard = activity.getSystemService(KeyguardManager.class);
        return keyguard != null && !keyguard.isDeviceLocked();
    }

    private static void validate(List<Uri> uris) {
        if (uris == null || uris.isEmpty() || uris.size() > 1000) {
            throw new IllegalArgumentException("invalid media batch");
        }
        HashSet<Uri> unique = new HashSet<>();
        for (Uri uri : uris) {
            if (uri == null || !"content".equals(uri.getScheme()) || !"media".equals(uri.getAuthority())
                    || uri.getQuery() != null || uri.getFragment() != null || !unique.add(uri)) {
                throw new IllegalArgumentException("not a distinct MediaStore URI");
            }
            List<String> path = uri.getPathSegments();
            if (path.size() != 4 || !path.get(0).matches("[A-Za-z0-9_-]+")
                    || path.get(0).equals("external") || path.get(0).equals("internal")
                    || !(path.get(1).equals("images") || path.get(1).equals("video"))
                    || !path.get(2).equals("media") || !path.get(3).matches("[1-9][0-9]*")
                    || Long.parseLong(path.get(3)) <= 0) {
                throw new IllegalArgumentException("not a concrete image/video URI");
            }
        }
    }

    /** Worker-only. Caller must revalidate row identity and actual write access afterward. */
    public static boolean request(Context context, List<Uri> uris, final Check check) {
        if (context == null || check == null || Looper.myLooper() == Looper.getMainLooper()) return false;
        final WriteFragment prompt = new WriteFragment();
        final Runnable cancellation = new Runnable() {
            public void run() {
                if (prompt.state == null) return;
                if (!prompt.state.pending() || !check.ready()) {
                    prompt.state.finish(false);
                    close(prompt);
                    return;
                }
                MAIN.postDelayed(this, 250);
            }
        };
        try {
            validate(uris);
            if (!check.ready()) return false;
            prompt.state = new MoveConsentState(() -> SystemClock.elapsedRealtime(), 180000);
            prompt.requests = new ArrayList<>(uris);
            prompt.check = check;
            MAIN.post(() -> {
                Activity activity = current.get();
                try {
                    if (!prompt.state.pending() || !check.ready() || !usable(activity)
                            || activity.getFragmentManager().isStateSaved()
                            || !context.getPackageName().equals(activity.getPackageName())) {
                        prompt.state.finish(false); return;
                    }
                    activity.getFragmentManager().beginTransaction().add(prompt,
                            "realme-gallery-write-" + System.identityHashCode(prompt)).commitNow();
                } catch (Exception failure) { prompt.state.finish(false); }
            });
            MAIN.post(cancellation);
            return prompt.state.await() && check.ready();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt(); return false;
        } catch (Exception unavailable) { return false;
        } finally {
            MAIN.removeCallbacks(cancellation);
            MAIN.post(() -> close(prompt));
        }
    }

    /** Also called by the pre-existing Move fragment; must run on the main thread. */
    public static void begin(Fragment fragment, MoveConsentState state, ArrayList<Uri> requests,
                             int writeRequest) {
        try {
            validate(requests);
            if (state == null || !state.pending() || !usable(fragment.getActivity())) {
                if (state != null) state.finish(false);
                close(fragment); return;
            }
            Flow flow = FLOWS.get(fragment);
            if (flow == null) {
                flow = new Flow(fragment, state, requests, writeRequest);
                FLOWS.put(fragment, flow);
                Context context = fragment.getActivity();
                boolean management = Build.VERSION.SDK_INT >= 31 && MediaStore.canManageMedia(context);
                boolean explained = preferences(context).getBoolean(EXPLAINED, false);
                flow.state.begin(management, explained || Build.VERSION.SDK_INT < 31);
            } else if (flow.consent != state || !flow.requests.equals(requests)
                    || flow.writeRequest != writeRequest) {
                throw new IllegalStateException("media request identity changed");
            }
            if (flow.state.step() == MediaConsentFlow.Step.OFFER) flow.offer();
            else if (flow.state.step() == MediaConsentFlow.Step.WRITE) flow.launchWrite();
        } catch (Exception unavailable) {
            if (state != null) state.finish(false);
            close(fragment);
        }
    }

    /** True consumes our Settings result; write-result handling stays in the calling fragment. */
    public static boolean result(Fragment fragment, MoveConsentState consent, int request, int result) {
        Flow flow = FLOWS.get(fragment);
        if (flow == null || flow.consent != consent) return request == SETTINGS_REQUEST;
        if (request == SETTINGS_REQUEST) {
            if (flow.state.step() != MediaConsentFlow.Step.SETTINGS) return true;
            try {
                if (!consent.pending() || !usable(fragment.getActivity())
                        || !flow.state.settingsReturned(MediaStore.canManageMedia(fragment.getActivity()))) {
                    consent.finish(false); close(fragment);
                } else flow.launchWrite();
            } catch (Exception unavailable) { consent.finish(false); close(fragment); }
            return true;
        }
        if (request == flow.writeRequest) {
            if (!flow.state.writeReturned()) return true;
            FLOWS.remove(fragment);
            flow.dismiss();
        }
        return false;
    }

    public static void destroy(Fragment fragment) {
        Flow flow = FLOWS.remove(fragment);
        if (flow != null) {
            flow.state.cancel(); flow.consent.finish(false); flow.dismiss();
        }
    }

    private static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static void close(Fragment fragment) {
        destroy(fragment);
        try {
            if (fragment != null && fragment.isAdded() && fragment.getFragmentManager() != null
                    && !fragment.getFragmentManager().isDestroyed()) {
                fragment.getFragmentManager().beginTransaction().remove(fragment).commitAllowingStateLoss();
            }
        } catch (IllegalStateException destroyedHost) { /* No pending mutation can resume. */ }
    }

    private static final class Flow {
        final WeakReference<Fragment> fragment;
        final MoveConsentState consent;
        final ArrayList<Uri> requests;
        final int writeRequest;
        final MediaConsentFlow state = new MediaConsentFlow();
        AlertDialog dialog;
        WeakReference<Activity> dialogHost = new WeakReference<>(null);
        boolean writeLaunched;

        Flow(Fragment fragment, MoveConsentState consent, ArrayList<Uri> requests, int writeRequest) {
            this.fragment = new WeakReference<>(fragment);
            this.consent = consent;
            this.requests = new ArrayList<>(requests);
            this.writeRequest = writeRequest;
        }

        void dismiss() {
            AlertDialog old = dialog;
            dialog = null;
            dialogHost = new WeakReference<>(null);
            if (old != null) {
                try { old.dismiss(); }
                catch (IllegalArgumentException | IllegalStateException destroyedWindow) { /* Host already detached. */ }
            }
        }

        void offer() {
            Fragment target = fragment.get();
            if (target == null || !consent.pending() || !usable(target.getActivity())) {
                consent.finish(false); return;
            }
            if (dialog != null && dialogHost.get() == target.getActivity() && dialog.isShowing()) return;
            dismiss();
            Activity activity = target.getActivity();
            dialogHost = new WeakReference<>(activity);
            dialog = new AlertDialog.Builder(activity)
                    .setTitle("Manage photos and videos")
                    .setMessage("Allow Photos to edit, move and delete your photos and videos without asking for each change. You can revoke this access in Android Settings.")
                    .setPositiveButton("Open settings", (d, which) -> {
                        preferences(activity).edit().putBoolean(EXPLAINED, true).apply();
                        if (!consent.pending() || !state.settings()) { close(target); return; }
                        try {
                            target.startActivityForResult(new Intent(Settings.ACTION_REQUEST_MANAGE_MEDIA,
                                    Uri.parse("package:" + activity.getPackageName())), SETTINGS_REQUEST);
                        } catch (Exception unavailable) { consent.finish(false); close(target); }
                    })
                    .setNegativeButton("Ask for this change", (d, which) -> {
                        preferences(activity).edit().putBoolean(EXPLAINED, true).apply();
                        if (consent.pending() && state.justThisChange()) launchWrite();
                    })
                    .setNeutralButton(android.R.string.cancel, (d, which) -> {
                        consent.finish(false); close(target);
                    })
                    .setOnCancelListener(d -> { consent.finish(false); close(target); })
                    .create();
            dialog.show();
        }

        void launchWrite() {
            if (writeLaunched || state.step() != MediaConsentFlow.Step.WRITE) return;
            Fragment target = fragment.get();
            try {
                if (target == null || !consent.pending() || !usable(target.getActivity())) {
                    consent.finish(false); return;
                }
                // Even with media management enabled, this platform request is required.
                // Android decides whether confirmation is necessary and grants row access.
                writeLaunched = true;
                target.startIntentSenderForResult(MediaStore.createWriteRequest(
                        target.getActivity().getContentResolver(), requests).getIntentSender(),
                        writeRequest, null, 0, 0, 0, null);
            } catch (Exception unavailable) { consent.finish(false); close(target); }
        }
    }

    public static final class WriteFragment extends Fragment {
        MoveConsentState state;
        ArrayList<Uri> requests;
        Check check;

        public WriteFragment() {}
        @Override public void onCreate(Bundle saved) { super.onCreate(saved); setRetainInstance(true); }
        @Override public void onResume() {
            super.onResume();
            if (check == null || !check.ready()) { if (state != null) state.finish(false); close(this); return; }
            begin(this, state, requests, WRITE_REQUEST);
        }
        @Override public void onActivityResult(int request, int result, Intent data) {
            super.onActivityResult(request, result, data);
            if (MediaWriteConsent.result(this, state, request, result)) return;
            if (request == WRITE_REQUEST && state != null) {
                state.finish(result == Activity.RESULT_OK && check != null && check.ready());
                close(this);
            }
        }
        @Override public void onDestroy() {
            destroy(this);
            if (state != null) state.finish(false);
            super.onDestroy();
        }
    }
}
