// SPDX-License-Identifier: Apache-2.0
// Compile to LLVM IR with the target's actual libgui flags and generated headers.
// Read-only ABI evidence. Never install or execute this probe on a device.
#include <gui/Surface.h>
#include <gui/IGraphicBufferConsumer.h>
#include <media/stagefright/foundation/AHandler.h>
#include <media/stagefright/foundation/ALooper.h>
#include <media/stagefright/foundation/AMessage.h>
#include <media/stagefright/foundation/ABuffer.h>
#include <type_traits>
#include <utility>

using namespace android;
static_assert(std::is_same_v<decltype(std::declval<const Surface&>()
        .getIGraphicBufferProducer()), sp<IGraphicBufferProducer>>);

extern "C" {
__attribute__((used)) unsigned long realme_layout_sizes[] = {
    sizeof(Surface), sizeof(GraphicBuffer), sizeof(RefBase), sizeof(AHandler),
    sizeof(ALooper), sizeof(AMessage), sizeof(ABuffer)
};
unsigned long realme_surface_window_offset() {
    auto* p = reinterpret_cast<Surface*>(0x1000UL);
    return reinterpret_cast<unsigned long>(static_cast<ANativeWindow*>(p)) - 0x1000UL;
}
int realme_producer_allow(IGraphicBufferProducer* p, bool enabled) {
    return p->allowAllocation(enabled);
}
int realme_surface_detach(Surface* p, sp<GraphicBuffer>* b, sp<Fence>* f) {
    return p->detachNextBuffer(b, f);
}
int realme_consumer_attach(IGraphicBufferConsumer* p, int* s, const sp<GraphicBuffer>& b) {
    return p->attachBuffer(s, b);
}
int realme_consumer_release(IGraphicBufferConsumer* p, int s, unsigned long n,
                            const sp<Fence>& f) {
    return p->releaseBuffer(s, n, f);
}
}
