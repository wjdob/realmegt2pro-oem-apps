// Host dependencies for the actual reviewed MountPartition body.
// No block devices, mounts, process launches or SELinux operations are performed.
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>
#include <sys/mount.h>
#include <sys/wait.h>
using std::string;
#define LOG(level) std::cerr
int wait_status=0,script_calls=0,rw_mounts=0,ro_mounts=0,unmount_calls=0;
int source_queries=0,source_reads=0,read_mode=0;
uint16_t source_mounts=1;
bool refuse_rw=false,refuse_set_context=false,refuse_clear_context=false;
bool refuse_source=false;
bool refuse_unmount=false,refuse_readonly=false,mounted=false,readonly_at_end=true,context_cleared=true;
int FakeSystem(const char*) { script_calls++;return wait_status; }
#define system FakeSystem
int setexeccon(const char* context) {
  if(context) { if(refuse_set_context)return -1;context_cleared=false; }
  else { if(refuse_clear_context)return -1;context_cleared=true; }
  return 0;
}
namespace brillo {using Blob=std::vector<uint8_t>;}
namespace utils {
bool FileExists(const char*) {return true;}
bool SetBlockDeviceReadOnly(const string&,bool value) {
  if(value && refuse_readonly)return false;
  readonly_at_end=value;return true;
}
bool MountFilesystem(const string&,const string&,unsigned long flags,const string&,const string&) {
  if(flags&MS_RDONLY) {ro_mounts++;mounted=true;return true;}
  rw_mounts++;if(refuse_rw)return false;mounted=true;return true;
}
bool UnmountFilesystem(const string&) {
  unmount_calls++;if(!mounted || refuse_unmount)return false;mounted=false;return true;
}
bool ReadFileChunk(const string&,int offset,int bytes,brillo::Blob* output) {
  source_reads++;
  if(offset!=0x434 || bytes!=6 || read_mode==1)return false;
  output->assign(read_mode==2?5:(read_mode==4?7:6),0);
  (*output)[0]=source_mounts&255;(*output)[1]=source_mounts>>8;
  if(read_mode!=3 && output->size()>=6) {(*output)[4]=0x53;(*output)[5]=0xef;}
  return true;
}
}
struct BootControlInterface {static constexpr int kInvalidSlot=-1;};
struct MockBootControl {bool GetPartitionDevice(const string& name,int,string* output){source_queries++;if(refuse_source)return false;*output="/source-"+name;return true;}};
struct MockHardware {string GetPartitionMountOptions(const string&){return "";}};
struct InstallPlan {int source_slot=0;struct Partition {string readonly_target_path="/target-system",name="system",filesystem_type="ext4";};};
class PostinstallRunnerAction {
 public: bool MountPartition(const InstallPlan::Partition&) noexcept;
 InstallPlan install_plan_;string fs_mount_dir_="/postinstall";MockBootControl boot;MockHardware hardware;
 MockBootControl* boot_control_=&boot;MockHardware* hardware_=&hardware;
 void EnsureUnmounted() {}
};
#include "BackuptoolMountPartition.cpp.inc"
int main(int argc,char** argv) {
 if(argc!=13)return 64;
 wait_status=std::atoi(argv[1]);source_mounts=std::atoi(argv[2]);refuse_rw=std::atoi(argv[3]);
 refuse_set_context=std::atoi(argv[4]);refuse_clear_context=std::atoi(argv[5]);
 refuse_unmount=std::atoi(argv[6]);refuse_readonly=std::atoi(argv[7]);
 PostinstallRunnerAction action;InstallPlan::Partition partition;
 partition.name=argv[8];partition.filesystem_type=argv[9];read_mode=std::atoi(argv[10]);
 refuse_source=std::atoi(argv[11]);action.install_plan_.source_slot=std::atoi(argv[12]);
 bool result=action.MountPartition(partition);
 std::cout<<"{\"mount_result\":"<<(result?"true":"false")<<",\"script_calls\":"<<script_calls
 <<",\"rw_mounts\":"<<rw_mounts<<",\"ro_mounts\":"<<ro_mounts<<",\"unmount_calls\":"<<unmount_calls
 <<",\"context_cleared\":"<<(context_cleared?"true":"false")
 <<",\"source_queries\":"<<source_queries<<",\"source_reads\":"<<source_reads
 <<",\"readonly_at_end\":"<<(readonly_at_end?"true":"false")<<"}\n";
}
