"""Check real JNI argument forwarding independently of the proprietary encoder."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import oem_camera_heif_transport as heif


@unittest.skipUnless(shutil.which('c++'), 'requires a 64-bit host C++ compiler')
class HeifForwardingTests(unittest.TestCase):
    def test_packet_fields_metadata_and_return_values(self):
        program = r'''
#include <cassert>
#include <cstdint>
#include "BRIDGE_SOURCE"
static const HeifPacket* expected;
extern "C" void heifProcess(int w,int h,int stride,int scanline,int fmt,int rot,
 unsigned char* buffer,const unsigned char* exif,unsigned int exifSize,
 unsigned char* debug,unsigned int debugSize,int fd,const void* parameters) {
 assert(w==expected->width && h==expected->height && stride==expected->stride);
 assert(scanline==expected->scanline && fmt==expected->format && rot==expected->rotation);
 assert(buffer==expected->buffer && exif==expected->exif && exifSize==expected->exifSize);
 assert(debug==expected->debug && debugSize==expected->debugSize && fd==expected->fd);
 assert(parameters==expected->parameters);
}
static AllocatePacket* allocation;
extern "C" int setBufferQAllocEnable(void* q,bool enabled,const void* parameters) {
 assert(q==allocation->queue && enabled==allocation->enabled);
 assert(parameters==allocation->parameters);return -43;
}
static ExchangePacket* exchange;
extern "C" int exchangeHardwareBufferBetweenBufferQ(void* s,void* t,int a,int b,const void* p) {
 assert(s==exchange->source && t==exchange->target);
 assert(a==exchange->sourceSlot && b==exchange->targetSlot);
 assert(p==exchange->parameters);return 71;
}
static AttachPacket* attachment;
extern "C" int attachHardwareBufferToBufferQ(void* q,void* b,const void* p) {
 assert(q==attachment->queue && b==attachment->buffer);
 assert(p==attachment->parameters);return -89;
}
int main() {
 assert(JNI_OnLoad(nullptr,nullptr)==0x00010006);
 unsigned char image[2],exif[3],debug[4];
 HeifPacket p{3072,4096,3200,4160,4,270,image,exif,3,debug,4,259,{11,22,33}};
 expected=&p;
 Java_android_hardware_camera2_OplusCameraManager_nativtSendToProcessHeif(nullptr,nullptr,
     reinterpret_cast<intptr_t>(&p));
 // Nonstandard/zero fields also pass unchanged; the glue never invents dimensions or fd.
 p.width=0;p.height=17;p.stride=-3;p.scanline=23;p.format=7;p.rotation=90;
 p.buffer=nullptr;p.exif=nullptr;p.exifSize=0;p.debug=nullptr;p.debugSize=0;p.fd=-1;
 Java_android_hardware_camera2_OplusCameraManager_nativtSendToProcessHeif(nullptr,nullptr,
     reinterpret_cast<intptr_t>(&p));
 AllocatePacket a{image,true,{1,2,3}};allocation=&a;
 assert(Java_android_hardware_camera2_OplusCameraManager_nativeSendToBufQAllocEnableEvent(
    nullptr,nullptr,reinterpret_cast<intptr_t>(&a))==-43);
 a.enabled=false;
 assert(Java_android_hardware_camera2_OplusCameraManager_nativeSendToBufQAllocEnableEvent(
    nullptr,nullptr,reinterpret_cast<intptr_t>(&a))==-43);
 ExchangePacket e{image,exif,-2,19,{4,5,6}};exchange=&e;
 assert(Java_android_hardware_camera2_OplusCameraManager_nativeSendToExchgHWBufBtwBufQEvent(
    nullptr,nullptr,reinterpret_cast<intptr_t>(&e))==71);
 AttachPacket t{image,debug,{7,8,9}};attachment=&t;
 assert(Java_android_hardware_camera2_OplusCameraManager_nativeSendToAttachHWBufToBufQEvent(
    nullptr,nullptr,reinterpret_cast<intptr_t>(&t))==-89);
}
'''.replace('BRIDGE_SOURCE', str(heif.SOURCE))
        with tempfile.TemporaryDirectory(prefix='heif-forwarding-') as directory:
            root = Path(directory)
            source, executable = root / 'test.cpp', root / 'test'
            source.write_text(program)
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', str(source), '-o', str(executable)],
                           check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)


class HeifInputTests(unittest.TestCase):
    def test_unknown_adapter_rejected_before_elf_parsing(self):
        with self.assertRaisesRegex(ValueError, 'unreviewed HEIF adapter'):
            heif.select_event_transport(b'not the pinned adapter',
                                        {'adapter_input_sha256': '0' * 64})


if __name__ == '__main__':
    unittest.main()
