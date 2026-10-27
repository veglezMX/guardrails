"""Materialize private configuration and an empty, immutable manifest mount."""
import os
import pathlib
import stat

for name in ("private", "state"):
    pathlib.Path("/" + name + "/data").mkdir(mode=0o700, exist_ok=True)
for source in pathlib.Path("/projected").iterdir():
    if not source.name.startswith("."):
        destination = pathlib.Path("/private/data", source.name)
        destination.write_bytes(source.read_bytes())
        destination.chmod(0o600)

manifests = pathlib.Path("/state/model-manifests-v2")
try:
    manifests.mkdir(mode=0o700)
except FileExistsError:
    created = False
else:
    created = True
try:
    descriptor = os.open(manifests, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
except OSError:
    raise RuntimeError("model_manifest_directory_invalid") from None
try:
    if created:
        # fsGroup-managed parents can propagate setgid even with mkdir(0700).
        # Normalize only the directory just created, never preexisting state.
        os.fchmod(descriptor, 0o700)
    metadata = os.fstat(descriptor)
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RuntimeError("model_manifest_directory_invalid")
    if os.listdir(descriptor):
        raise RuntimeError("model_manifest_directory_not_empty")
finally:
    os.close(descriptor)
