package com.oplus.ocs.camera.metadata.parameter;

import android.hardware.camera2.CaptureRequest;
import android.view.Surface;

/** Compile-only ABI stub. The authentic SDK interface is the runtime definition. */
public interface ICaptureRequestBuilderInternal {
    boolean setParameter(CaptureRequest.Key key, Object value);
    Object getParameter(CaptureRequest.Key key);
    void addTarget(Surface surface);
    void removeTarget(Surface surface);
    void setTag(Object tag);
    CaptureRequest build();
}
