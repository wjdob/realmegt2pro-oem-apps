// SPDX-License-Identifier: Apache-2.0
// Optional Camera JNI glue. The pinned OEM helpers retain buffer/encoding work.
// Explicit JNI_OnLoad prevents dlsym from finding the writer dependency's loader
// callback in a boot-classloader context where its app-scoped Java class is absent.

#define EXPORT extern "C" __attribute__((visibility("default")))
extern "C" void heifProcess(int, int, int, int, int, int,
        unsigned char*, const unsigned char*, unsigned int, unsigned char*,
        unsigned int, int, const void*);
extern "C" int setBufferQAllocEnable(void*, bool, const void*);
extern "C" int exchangeHardwareBufferBetweenBufferQ(void*, void*, int, int, const void*);
extern "C" int attachHardwareBufferToBufferQ(void*, void*, const void*);

struct HeifPacket {
    int width, height, stride, scanline, format, rotation;
    unsigned char* buffer;
    const unsigned char* exif;
    unsigned int exifSize;
    unsigned char* debug;
    unsigned int debugSize;
    int fd;
    // The original helper receives this vector by address. Keep its storage
    // opaque; do not read, rewrite or instantiate vendor C++ container types.
    unsigned long parameters[3];
};
struct AllocatePacket { void* queue; bool enabled; unsigned long parameters[3]; };
struct ExchangePacket { void* source; void* target; int sourceSlot; int targetSlot;
    unsigned long parameters[3]; };
struct AttachPacket { void* queue; void* buffer; unsigned long parameters[3]; };
static_assert(sizeof(void*) == 8);
static_assert(__builtin_offsetof(HeifPacket, buffer) == 0x18);
static_assert(__builtin_offsetof(HeifPacket, exifSize) == 0x28);
static_assert(__builtin_offsetof(HeifPacket, debug) == 0x30);
static_assert(__builtin_offsetof(HeifPacket, fd) == 0x3c);
static_assert(__builtin_offsetof(HeifPacket, parameters) == 0x40);
static_assert(sizeof(HeifPacket) == 0x58);
static_assert(__builtin_offsetof(AllocatePacket, parameters) == 0x10);
static_assert(__builtin_offsetof(ExchangePacket, parameters) == 0x18);
static_assert(__builtin_offsetof(AttachPacket, parameters) == 0x10);

EXPORT int JNI_OnLoad(void*, void*) { return 0x00010006; }

// These trusted native-event entry points preserve the original pointer/ABI
// contract. The actual frame, format, rotation, fd and metadata are forwarded.
EXPORT void Java_android_hardware_camera2_OplusCameraManager_nativtSendToProcessHeif(
        void*, void*, long long address) {
    const auto* p = reinterpret_cast<const HeifPacket*>(address);
    heifProcess(p->width, p->height, p->stride, p->scanline, p->format, p->rotation,
            p->buffer, p->exif, p->exifSize, p->debug, p->debugSize, p->fd, p->parameters);
}
EXPORT int Java_android_hardware_camera2_OplusCameraManager_nativeSendToBufQAllocEnableEvent(
        void*, void*, long long address) {
    const auto* p = reinterpret_cast<const AllocatePacket*>(address);
    return setBufferQAllocEnable(p->queue, p->enabled, p->parameters);
}
EXPORT int Java_android_hardware_camera2_OplusCameraManager_nativeSendToExchgHWBufBtwBufQEvent(
        void*, void*, long long address) {
    const auto* p = reinterpret_cast<const ExchangePacket*>(address);
    return exchangeHardwareBufferBetweenBufferQ(p->source, p->target,
            p->sourceSlot, p->targetSlot, p->parameters);
}
EXPORT int Java_android_hardware_camera2_OplusCameraManager_nativeSendToAttachHWBufToBufQEvent(
        void*, void*, long long address) {
    const auto* p = reinterpret_cast<const AttachPacket*>(address);
    return attachHardwareBufferToBufferQ(p->queue, p->buffer, p->parameters);
}
