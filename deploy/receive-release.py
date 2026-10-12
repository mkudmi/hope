"""Forced SSH command: accept only a release archive for a full commit SHA."""

import fcntl
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path("/opt/hope")


def main():
    match = re.fullmatch(r"deploy ([0-9a-f]{40})( probe)?", os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    if not match:
        raise SystemExit("Only deploy <commit SHA> is allowed")
    sha = match[1]
    with (ROOT / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        release = ROOT / "releases" / sha
        if release.exists():
            # Failed/repeated releases are immutable; use the existing source.
            while sys.stdin.buffer.read(65536):
                pass
        else:
            release.mkdir(mode=0o700)
            try:
                with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
                    for member in archive:
                        path = PurePosixPath(member.name)
                        if (
                            path.is_absolute()
                            or ".." in path.parts
                            or not (member.isfile() or member.isdir())
                            or any(part in (".env", ".git", "data", "artifacts") for part in path.parts)
                        ):
                            raise ValueError("Unsafe release archive member")
                        archive.extract(member, path=release)
                (release / ".received").touch()
            except (OSError, ValueError, tarfile.TarError):
                raise SystemExit("Release extraction failed; use a new commit SHA") from None
        if not (release / ".received").exists():
            raise SystemExit("Incomplete release; use a new commit SHA")
        subprocess.run(
            ["bash", str(release / "deploy/deploy-server.sh"), sha, "probe" if match[2] else "standby"],
            check=True,
        )


if __name__ == "__main__":
    main()
