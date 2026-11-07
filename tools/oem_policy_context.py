"""Explicit API37 policy context. Legacy policy inputs remain unchanged."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

import oem_ota_compat
import oem_platform

ROOT = Path(__file__).resolve().parents[1]
COMPILER_INPUTS = oem_ota_compat.COMPILER_INPUTS37
RUNTIME_INPUTS = oem_ota_compat.policy_inputs_for({'platform': oem_platform.ANDROID17})
PATCH_PATHS = oem_ota_compat.POLICY
PRODUCT_MAPPING = 'product/etc/selinux/mapping/202604.cil'
PRECOMPILED_DIGEST = 'odm/etc/selinux/precompiled_sepolicy.product_sepolicy_and_mapping.sha256'
INPUTS = RUNTIME_INPUTS | PATCH_PATHS | {PRECOMPILED_DIGEST}
CAMERA_FRAGMENT_SHA = '9b1be865b858ebe608e54c9452d4d50ac0724cc37129447cc728bc995d43d256'
GALLERY_FRAGMENT_SHA = '8e2ddcfb510d6adbec2cbcbb0c6c469bf4249ecf6c71c8704c5d3b5e1e6e92f0'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_config(data):
    expected = {'schema', 'platform', 'model', 'mapping_version', 'genfs_labels_version', 'source_revision',
                'target_files_sha256', 'build_incremental', 'inputs', 'compiler_inputs',
                'camera_fragment_sha256', 'gallery_fragment_sha256', 'compiler', 'runtime_inputs'}
    if (not isinstance(data, dict) or set(data) != expected or type(data['schema']) is not int or data['schema'] != 1
            or oem_platform.explicit(data['platform']) != oem_platform.ANDROID17
            or data['model'] != 'RMX3301' or data['mapping_version'] != '202604'
            or data['genfs_labels_version'] != '202604'
            or not re.fullmatch('[0-9a-f]{40}', data['source_revision'])
            or not re.fullmatch('[0-9a-f]{64}', data['target_files_sha256'])
            or not re.fullmatch(r'RMX3301\.[0-9]+\.[0-9a-f]+', data['build_incremental'])
            or not isinstance(data['inputs'], dict) or set(data['inputs']) != INPUTS
            or any(not re.fullmatch('[0-9a-f]{64}', v) for v in data['inputs'].values())
            or not isinstance(data['compiler_inputs'], list)
            or len(data['compiler_inputs']) != len(COMPILER_INPUTS)
            or set(data['compiler_inputs']) != COMPILER_INPUTS
            or not isinstance(data['runtime_inputs'], list)
            or len(data['runtime_inputs']) != len(RUNTIME_INPUTS)
            or set(data['runtime_inputs']) != RUNTIME_INPUTS
            or data['camera_fragment_sha256'] != CAMERA_FRAGMENT_SHA
            or data['gallery_fragment_sha256'] != GALLERY_FRAGMENT_SHA
            or not isinstance(data['compiler'], dict)
            or set(data['compiler']) != {'sha256', 'policy_version'}
            or not re.fullmatch('[0-9a-f]{64}', data['compiler']['sha256'])
            or data['compiler']['policy_version'] != 30):
        raise ValueError('unreviewed API37 policy context or scope')
    return data


@dataclass(frozen=True)
class Context:
    raw: bytes
    expected_sha256: str

    @property
    def config(self):
        if sha(self.raw) != self.expected_sha256:
            raise ValueError('policy context bytes changed')
        return checked_config(json.loads(self.raw))


def load(spec):
    if (not isinstance(spec, dict) or set(spec) != {'source', 'sha256'}
            or not isinstance(spec['source'], str)
            or not re.fullmatch('[0-9a-f]{64}', spec['sha256'])):
        raise ValueError('explicit hash-bound policy context required')
    relative = PurePosixPath(spec['source'])
    if (len(relative.parts) != 2 or relative.parts[0] != 'configs'
            or not re.fullmatch(r'[A-Za-z0-9_.+-]+\.json', relative.parts[1])):
        raise ValueError('policy context must be a reviewed configs JSON file')
    path = ROOT / relative
    if path.is_symlink() or not path.resolve().is_relative_to((ROOT/'configs').resolve()):
        raise ValueError('policy context escapes configs')
    context = Context(path.read_bytes(), spec['sha256'])
    context.config  # Validate before returning an immutable source identity.
    return context
