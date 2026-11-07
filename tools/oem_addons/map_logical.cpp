// Prepare missing logical devices through the existing recovery fs_mgr API.
// No partition contents/metadata, BCB, slots, protection flags or mounts are changed here.
#include <liblp/liblp.h>
#include <sys/stat.h>
#include <sys/system_properties.h>
#include <fcntl.h>
#include <unistd.h>
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <memory>
#include <string>
#include <vector>

namespace android::fs_mgr {
std::unique_ptr<LpMetadata> ReadCurrentMetadata(const std::string&);
bool CreateLogicalPartitions(const LpMetadata&, const std::string&);
}
namespace {
std::string prop(const char* name) {
    char value[PROP_VALUE_MAX] = {};
    __system_property_get(name, value);
    return value;
}
bool block(const std::string& path) {
    struct stat st{};
    return stat(path.c_str(), &st) == 0 && S_ISBLK(st.st_mode);
}
bool identity(const std::string& name) {
    const std::string path = "/dev/block/mapper/" + name;
    char resolved[4096];
    if (!block(path) || !realpath(path.c_str(), resolved)) return false;
    const char* base = strrchr(resolved, '/');
    if (!base) return false;
    const auto sys = std::string("/sys/class/block/") + (base + 1) + "/dm/name";
    const int fd = open(sys.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) return false;
    char value[128]{};
    const auto count = read(fd, value, sizeof(value) - 1);
    close(fd);
    return count == static_cast<ssize_t>(name.size() + 1) && value[count - 1] == '\n' &&
           std::string(value, count - 1) == name;
}
bool idle_vab() {
    unsigned char bytes[7]{};
    const int fd = open("/dev/block/by-name/misc", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return false;
    const auto count = pread(fd, bytes, sizeof(bytes), 32768);
    close(fd);
    const unsigned char expected[] = {2, 176, 10, 116, 86, 0};
    return count == sizeof(bytes) && !memcmp(bytes, expected, sizeof(expected)) && bytes[6] <= 1;
}
int fail(const char* text) {
    fprintf(stderr, "logical preparation refused: %s\n", text);
    return 1;
}
}
int main(int argc, char** argv) {
    if (argc != 2 || strcmp(argv[1], "--prepare-missing")) return fail("unknown operation");
    if (getuid() || prop("ro.bootmode") != "recovery" ||
        prop("ro.product.model") != "RMX3301" || prop("ro.bionic.arch") != "arm64" ||
        prop("ro.boot.vbmeta.device_state") != "unlocked" ||
        prop("ro.boot.dynamic_partitions") != "true") return fail("recovery identity");
    const auto suffix = prop("ro.boot.slot_suffix");
    if ((suffix != "_a" && suffix != "_b") || !idle_vab()) return fail("slot or active/unknown VAB state");
    const std::string super = "/dev/block/by-name/super";
    if (!block(super)) return fail("super is not a block device");
    auto metadata = android::fs_mgr::ReadCurrentMetadata(super);
    if (!metadata) return fail("cannot read current validated metadata");
    std::vector<std::string> required;
    for (const char* part : {"system", "product", "system_ext", "vendor", "odm"}) {
        const std::string name = part + suffix;
        bool found = false;
        for (const auto& entry : metadata->partitions) {
            if (android::fs_mgr::GetPartitionName(entry) == name && entry.num_extents &&
                !(entry.attributes & LP_PARTITION_ATTR_DISABLED)) found = true;
        }
        if (!found) return fail("required current-slot partition absent/disabled");
        required.push_back(name);
    }
    unsigned present = 0;
    for (const auto& name : required) {
        struct stat st{};
        const auto path = "/dev/block/mapper/" + name;
        if (lstat(path.c_str(), &st) == 0) {
            if (!identity(name)) return fail("existing mapper identity");
            ++present;
        } else if (errno != ENOENT) return fail("cannot inspect mapper");
    }
    if (present == required.size()) {
        puts("logical devices already prepared; no mapping changes");
        return 0;
    }
    if (present) return fail("partial existing mappings require review");
    // Refuse any pre-existing metadata device: never recreate/remap a protected device.
    for (const auto& entry : metadata->partitions) {
        if (!entry.num_extents || (entry.attributes & LP_PARTITION_ATTR_DISABLED)) continue;
        const auto name = android::fs_mgr::GetPartitionName(entry);
        struct stat st{};
        if (lstat(("/dev/block/mapper/" + name).c_str(), &st) == 0)
            return fail("metadata device already exists");
        if (errno != ENOENT) return fail("cannot inspect metadata device");
    }
    // Exact API used by recovery_utils/roots.cpp map_logical_partitions(). Recovery's
    // own build determines mapping attributes. Never retry or weaken a failed rw mount.
    if (!idle_vab()) return fail("VAB changed before mapping");
    if (!android::fs_mgr::CreateLogicalPartitions(*metadata, super)) return fail("standard recovery mapping failed");
    for (const auto& name : required) if (!identity(name)) return fail("mapped identity missing");
    puts("logical devices prepared by standard recovery fs_mgr");
    return 0;
}
