// SPDX-License-Identifier: Apache-2.0
// Android 17 implementation of the optional Camera's three buffer events.
// The OEM helper embeds older Surface/BufferQueue vtable slots and the removed
// five-argument consumer releaseBuffer ABI. Build with the target's headers.

#include <gui/IGraphicBufferConsumer.h>
#include <gui/Surface.h>
#include <private/android/AHardwareBufferHelpers.h>
#include <ui/Fence.h>
#include <utils/Errors.h>

#define EXPORT extern "C" __attribute__((visibility("default")))

namespace {
android::sp<android::Surface> surfaceFromWindow(void* window) {
    // The event supplies an ANativeWindow belonging to an Android Surface.
    // Let the target compiler apply the base adjustment; keep no numeric offset.
    if (!window) return nullptr;
    auto* surface = static_cast<android::Surface*>(static_cast<ANativeWindow*>(window));
    // Match the original helper's incStrong/decStrong acquisition. fromExisting
    // would add a new incStrongRequireStrong precondition to the OEM event ABI.
    return android::sp<android::Surface>(surface);
}

int attachAndRelease(android::IGraphicBufferConsumer* consumer,
                     const android::sp<android::GraphicBuffer>& buffer) {
    int slot = -1;
    int result = consumer->attachBuffer(&slot, buffer);
    if (result != android::OK) return result;
    // Preserve the original event's frame number and no-fence release semantics.
    return consumer->releaseBuffer(slot, 0, android::Fence::NO_FENCE);
}
}

EXPORT int setBufferQAllocEnable(void* window, bool enabled, const void*) {
    auto surface = surfaceFromWindow(window);
    if (!surface) return android::BAD_VALUE;
    auto producer = surface->getIGraphicBufferProducer();
    if (!producer) return android::NO_INIT;
    // The original event returns zero even if allowAllocation fails. Preserve
    // that return contract; exchange/attach events propagate their own errors.
    (void)producer->allowAllocation(enabled);
    return android::OK;
}

EXPORT int exchangeHardwareBufferBetweenBufferQ(void* targetConsumer,
        void* sourceWindow, int, int, const void*) {
    if (!targetConsumer || !sourceWindow) return -1;
    auto surface = surfaceFromWindow(sourceWindow);
    android::sp<android::GraphicBuffer> buffer;
    android::sp<android::Fence> fence;
    int result = surface->detachNextBuffer(&buffer, &fence);
    if (result != android::OK) return result;
    return attachAndRelease(static_cast<android::IGraphicBufferConsumer*>(targetConsumer),
                            buffer);
}

EXPORT int attachHardwareBufferToBufferQ(void* targetConsumer,
        void* hardwareBuffer, const void*) {
    if (!targetConsumer || !hardwareBuffer) return android::BAD_VALUE;
    auto* raw = android::AHardwareBuffer_to_GraphicBuffer(
            static_cast<AHardwareBuffer*>(hardwareBuffer));
    if (!raw) return android::BAD_VALUE;
    android::sp<android::GraphicBuffer> buffer(raw);
    return attachAndRelease(static_cast<android::IGraphicBufferConsumer*>(targetConsumer),
                            buffer);
}
