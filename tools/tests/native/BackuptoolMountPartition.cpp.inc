//
// Copyright (C) 2011 The Android Open Source Project
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//

bool PostinstallRunnerAction::MountPartition(
    const InstallPlan::Partition& partition) noexcept {
  // Perform post-install for the current_partition_ partition. At this point we
  // need to call CompletePartitionPostinstall to complete the operation and
  // cleanup.
  const auto mountable_device = partition.readonly_target_path;
  if (!utils::FileExists(mountable_device.c_str())) {
    LOG(ERROR) << "Mountable device " << mountable_device << " for partition "
               << partition.name << " does not exist";
    return false;
  }

  if (!utils::FileExists(fs_mount_dir_.c_str())) {
    LOG(ERROR) << "Mount point " << fs_mount_dir_
               << " does not exist, mount call will fail";
    return false;
  }
  // Double check that the fs_mount_dir is not busy with a previous mounted
  // filesystem from a previous crashed postinstall step.
  EnsureUnmounted();

#ifdef __ANDROID__
  bool backuptool_failed = false;
#if !defined(__ANDROID_RECOVERY__) && defined(RUN_BACKUPTOOL)
  // Check the currently installed /system partition to see if it's ever
  // been mounted R/W. If it has, we'll run backuptool scripts for it
  // since we can safely assume something on the partition has been
  // changed and we won't be breaking verity (since it's already been
  // broken). If it hasn't ever been mounted R/W, we can assume that
  // the rom that the user is upgrading to will have everything they
  // need and no addon.d scripts will need to be run to retain stuff
  // after the upgrade.
  //
  // Use the following disk layout info to make the determination
  // https://ext4.wiki.kernel.org/index.php/Ext4_Disk_Layout
  // Super block starts from block 0, offset 0x400
  //   0x2C: len32 Mount time
  //   0x30: len32 Write time
  //   0x34: len16 Number of mounts since the last fsck
  //   0x38: len16 Magic signature 0xEF53

  string source_path;

  // Only the system ext4 source can own these add-on hooks. Other partitions
  // (notably EROFS vendor) do not have an ext4 mount counter at this offset.
  if (partition.name == "system" && partition.filesystem_type == "ext4" &&
      install_plan_.source_slot != BootControlInterface::kInvalidSlot) {
    if (!boot_control_->GetPartitionDevice(
            partition.name, install_plan_.source_slot, &source_path) ||
        source_path.empty()) {
      LOG(ERROR) << "Failed to resolve source system for backuptool";
      return false;
    }
  }

  uint16_t mount_count = 0;

  if (!source_path.empty()) {
    brillo::Blob chunk;

    // Read mount count, maximum mount count and the ext4 magic together.
    // Fail on unreadable/truncated state rather than losing selected add-ons.
    if (!utils::ReadFileChunk(source_path, 0x400 + 0x34, 6, &chunk) ||
        chunk.size() != 6) {
      LOG(ERROR) << "Failed to read source superblock for backuptool";
      return false;
    }
    if (chunk[4] == 0x53 && chunk[5] == 0xef) {
      mount_count = static_cast<uint16_t>(
          chunk[0] | (static_cast<uint16_t>(chunk[1]) << 8));
    } else {
      LOG(INFO) << "Non-ext4 source has no ext4 backuptool mount counter";
    }
  }

  LOG(INFO) << source_path << " has been mounted R/W " << mount_count << " times.";

  if (mount_count > 0) {
    if (!utils::SetBlockDeviceReadOnly(mountable_device, false)) {
      LOG(ERROR) << "Error marking the device " << mountable_device << " writeable.";
      return false;
    }
    // Mount the target partition R/W
    LOG(INFO) << "Running backuptool scripts";
    if (!utils::MountFilesystem(mountable_device, fs_mount_dir_,
                                MS_NOATIME | MS_NODEV | MS_NODIRATIME,
                                partition.filesystem_type, "seclabel")) {
      LOG(ERROR) << "Failed to mount target for backuptool";
      utils::SetBlockDeviceReadOnly(mountable_device, true);
      return false;
    }

    // The execution context applies to the child, not this process.
    if (setexeccon("u:r:backuptool:s0")) {
      LOG(ERROR) << "Failed to set backuptool context";
      backuptool_failed = true;
    } else {
      int ret = system("/postinstall/system/bin/backuptool_postinstall.sh");
      backuptool_failed = ret == -1 || !WIFEXITED(ret) || WEXITSTATUS(ret) != 0;
      if (backuptool_failed) {
        LOG(ERROR) << "Backuptool postinstall step failed. ret=" << ret;
      }
    }

    // Clear the child context and restore the target even on a helper error.
    if (setexeccon(nullptr)) {
      LOG(ERROR) << "Failed to clear backuptool context";
      backuptool_failed = true;
    }
    if (!utils::UnmountFilesystem(fs_mount_dir_)) {
      LOG(ERROR) << "Failed to unmount backuptool target";
      backuptool_failed = true;
    }
  } else {
    LOG(INFO) << "Skipping backuptool scripts";
  }

#endif  // !__ANDROID_RECOVERY__ && RUN_BACKUPTOOL

  // In Chromium OS, the postinstall step is allowed to write to the block
  // device on the target image, so we don't mark it as read-only and should
  // be read-write since we just wrote to it during the update.

  // Mark the block device as read-only before mounting for post-install.
  if (!utils::SetBlockDeviceReadOnly(mountable_device, true)) {
    return false;
  }
  if (backuptool_failed) {
    LOG(ERROR) << "Backuptool failure stops OTA activation";
    return false;
  }
#endif  // __ANDROID__

  if (!utils::MountFilesystem(
          mountable_device,
          fs_mount_dir_,
          MS_RDONLY,
          partition.filesystem_type,
          hardware_->GetPartitionMountOptions(partition.name))) {
    return false;
  }
  return true;
}
