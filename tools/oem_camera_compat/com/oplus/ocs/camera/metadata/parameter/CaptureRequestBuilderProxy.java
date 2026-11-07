package com.oplus.ocs.camera.metadata.parameter;

import android.hardware.camera2.CaptureRequest;
import android.view.Surface;
import realme.camera.compat.SessionCaller;

/** Pinned SDK proxy ABI; retain its null/exception behavior and add real caller metadata. */
public final class CaptureRequestBuilderProxy implements ICaptureRequestBuilderInternal {
    private CaptureRequest.Builder mBuilder;

    public CaptureRequestBuilderProxy(CaptureRequest.Builder builder) { mBuilder = builder; }
    public boolean setParameter(CaptureRequest.Key key, Object value) {
        try {
            if (mBuilder != null) lambda$setParameter$0(key, value, mBuilder);
            return true;
        } catch (Exception error) { return false; }
    }
    public Object getParameter(CaptureRequest.Key key) {
        return mBuilder == null ? null : lambda$getParameter$1(key, mBuilder);
    }
    public void addTarget(Surface surface) {
        if (mBuilder != null) lambda$addTarget$2(surface, mBuilder);
    }
    public void removeTarget(Surface surface) {
        if (mBuilder != null) lambda$removeTarget$3(surface, mBuilder);
    }
    public void setTag(Object tag) {
        if (mBuilder != null) lambda$setTag$4(tag, mBuilder);
    }
    public CaptureRequest build() {
        return mBuilder == null ? null : lambda$build$5(mBuilder);
    }
    // Retain the original SDK's package-visible synthetic entry points: its pinned
    // existing lambda classes reference them even though this implementation is direct.
    static void lambda$setParameter$0(CaptureRequest.Key key, Object value,
                                     CaptureRequest.Builder builder) { builder.set(key, value); }
    static Object lambda$getParameter$1(CaptureRequest.Key key,
                                        CaptureRequest.Builder builder) { return builder.get(key); }
    static void lambda$addTarget$2(Surface surface, CaptureRequest.Builder builder) {
        builder.addTarget(surface);
    }
    static void lambda$removeTarget$3(Surface surface, CaptureRequest.Builder builder) {
        builder.removeTarget(surface);
    }
    static void lambda$setTag$4(Object tag, CaptureRequest.Builder builder) { builder.setTag(tag); }
    static CaptureRequest lambda$build$5(CaptureRequest.Builder builder) {
        SessionCaller.apply(builder);
        return builder.build();
    }
}
