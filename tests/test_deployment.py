import io
import os
import runpy
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="SSH receiver requires Linux flock")
SHA = "a" * 40


def receiver(tmp_path, monkeypatch, command, member="deploy/deploy-server.sh", symlink=False):
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "deploy/receive-release.py"))
    module["main"].__globals__["ROOT"] = tmp_path
    (tmp_path / "releases").mkdir()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        item = tarfile.TarInfo(member)
        if symlink:
            item.type, item.linkname = tarfile.SYMTYPE, "/etc/passwd"
        else:
            item.size = 4
        archive.addfile(item, None if symlink else io.BytesIO(b"test"))
    stream.seek(0)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=stream))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command)
    execute = Mock()
    monkeypatch.setattr("subprocess.run", execute)
    return module["main"], execute


@pytest.mark.parametrize("mode", ["", " probe"])
def test_receiver_accepts_sha_and_explicit_probe(tmp_path, monkeypatch, mode):
    main, execute = receiver(tmp_path, monkeypatch, "deploy " + SHA + mode)
    main()
    assert (tmp_path / "releases" / SHA / ".received").exists()
    assert execute.call_args.args[0][-1] == ("probe" if mode else "standby")


@pytest.mark.parametrize("command", ["bash", "deploy main", "deploy " + SHA + "; id"])
def test_receiver_rejects_other_commands(tmp_path, monkeypatch, command):
    main, execute = receiver(tmp_path, monkeypatch, command)
    with pytest.raises(SystemExit):
        main()
    execute.assert_not_called()


@pytest.mark.parametrize("member,symlink", [("../escape", False), (".env", False), ("link", True)])
def test_receiver_rejects_unsafe_archive(tmp_path, monkeypatch, member, symlink):
    main, execute = receiver(tmp_path, monkeypatch, "deploy " + SHA, member, symlink)
    with pytest.raises(SystemExit):
        main()
    execute.assert_not_called()
    assert not (tmp_path / "releases" / SHA / ".received").exists()
