"""Release images built locally while GitHub Actions is off (tools/release_image.py)."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import release_image  # noqa: E402


def _calls(monkeypatch, outputs):
    seen = []
    monkeypatch.setattr(release_image, "run", lambda cmd, **kw: seen.append(cmd))
    monkeypatch.setattr(release_image, "out", lambda cmd, **kw: outputs.get(cmd[1], ""))
    return seen


def test_the_registry_gets_version_minor_and_latest_for_both_platforms(monkeypatch):
    seen = _calls(monkeypatch, {})
    release_image.registry("0.29.0", "ghcr.io/mags-ob/dexstash", True, Path("src"))
    cmd = seen[0]
    assert cmd[cmd.index("--platform") + 1] == "linux/amd64,linux/arm64" and "--push" in cmd
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--tag"] == [
        "ghcr.io/mags-ob/dexstash:0.29.0", "ghcr.io/mags-ob/dexstash:0.29", "ghcr.io/mags-ob/dexstash:latest"]


def test_a_pre_release_is_not_latest(monkeypatch):
    seen = _calls(monkeypatch, {})
    release_image.registry("0.30.0", "ghcr.io/mags-ob/dexstash", False, Path("src"))
    assert not any(a.endswith(":latest") for a in seen[0])


def test_the_image_name_is_the_owner_in_lower_case_as_in_compose(monkeypatch):
    _calls(monkeypatch, {"repo": "Mags-Ob"})
    assert release_image.image_name() == "ghcr.io/mags-ob/dexstash"
    assert "image: ghcr.io/mags-ob/dexstash:" in (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_a_tag_that_does_not_match_the_version_stops(monkeypatch, tmp_path):
    src = tmp_path / "src"
    (src / "app").mkdir(parents=True)
    (src / "app" / "__init__.py").write_text('__version__ = "0.29.0"\n', encoding="utf-8")
    monkeypatch.setattr(release_image.shutil, "which", lambda tool: tool)
    monkeypatch.setattr(release_image, "release_info", lambda tag: {"isDraft": False, "isPrerelease": False})
    monkeypatch.setattr(release_image, "image_name", lambda: "ghcr.io/mags-ob/dexstash")
    monkeypatch.setattr(release_image, "checkout", lambda tag, folder: src)
    built = []
    monkeypatch.setattr(release_image, "builder", lambda: built.append(True))
    with pytest.raises(SystemExit, match="tag v0.29 but app/__init__.py says 0.29.0"):
        release_image.main(["v0.29"])
    assert not built


def test_a_draft_release_stops(monkeypatch):
    def view(cmd, **kw):
        return '{"isDraft": true, "isPrerelease": false}'
    monkeypatch.setattr(release_image, "out", view)
    with pytest.raises(SystemExit, match="still a draft"):
        release_image.release_info("v0.29.0")


def test_no_release_stops(monkeypatch):
    def view(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(release_image, "out", view)
    with pytest.raises(SystemExit, match="no GitHub Release"):
        release_image.release_info("v0.29.0")
