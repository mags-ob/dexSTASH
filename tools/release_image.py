"""Builds a release's Docker image on your own machine and publishes it, for when GitHub Actions is switched off
(it does what the Image workflow does).

    python tools/release_image.py v0.29.0        # a published Release's tag
    python tools/release_image.py v0.29.0 --files-only
    python tools/release_image.py v0.29.0 --registry-only

For the tag's code (checked out in a temporary folder, so the branch you're on and any changes you haven't
committed don't matter) it:
- checks the tag matches the app's version, and that the Release is published;
- builds the image for amd64 and arm64 and attaches each as a file to the Release
  (dexstash-<version>-<arch>.tar.gz and its .sha256), for `docker load`;
- pushes both to ghcr.io/<owner>/dexstash as <version>, <major.minor> and latest (not latest for a
  pre-release), for `docker compose pull`.

Needs Docker with buildx (Docker Desktop has both, and runs the arm64 build by emulation; on Linux run
`docker run --privileged --rm tonistiigi/binfmt --install arm64` once) and the GitHub CLI signed in with
package rights: `gh auth refresh -s write:packages`. The arm64 build is emulated on an Intel or AMD machine, so
it takes a while. Run it after the release's pull request has merged, so its tests and checks have run.
"""

import gzip
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHES = ["amd64", "arm64"]
BUILDER = "dexstash-release"


def run(cmd, **kw):
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def out(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout.strip()


def fail(message):
    sys.exit(f"Stopped: {message}")


def release_info(tag):
    try:
        info = json.loads(out(["gh", "release", "view", tag, "--json", "isDraft,isPrerelease"], cwd=ROOT))
    except subprocess.CalledProcessError:
        fail(f"there's no GitHub Release for {tag}. Publish it first (Releases › the draft › Publish).")
    if info["isDraft"]:
        fail(f"the Release for {tag} is still a draft. Publish it first, so the tag exists.")
    return info


def checkout(tag, folder):
    """The tag's files, from GitHub, in their own folder."""
    run(["git", "fetch", "--force", "origin", f"refs/tags/{tag}:refs/tags/{tag}"], cwd=ROOT)
    archive = folder / "src.tar"
    run(["git", "archive", "--format=tar", "-o", str(archive), tag], cwd=ROOT)
    src = folder / "src"
    src.mkdir()
    shutil.unpack_archive(archive, src)
    return src


def app_version(src):
    found = re.search(r'__version__ = "(.+?)"', (src / "app" / "__init__.py").read_text(encoding="utf-8"))
    return found.group(1) if found else ""


def image_name():
    """As in docker-compose.yml: registry names are lower case."""
    owner = out(["gh", "repo", "view", "--json", "owner", "-q", ".owner.login"], cwd=ROOT)
    return f"ghcr.io/{owner.lower()}/dexstash"


def builder():
    """A buildx builder that can build for another architecture and write an image file."""
    if subprocess.run(["docker", "buildx", "inspect", BUILDER], capture_output=True).returncode != 0:
        run(["docker", "buildx", "create", "--name", BUILDER, "--driver", "docker-container"])
    run(["docker", "buildx", "inspect", "--bootstrap", BUILDER])


def sign_in():
    login = out(["gh", "api", "user", "-q", ".login"])
    token = out(["gh", "auth", "token"])
    print(f"$ docker login ghcr.io -u {login} --password-stdin", flush=True)
    subprocess.run(["docker", "login", "ghcr.io", "-u", login, "--password-stdin"], input=token, text=True,
                   check=True)


def files(tag, version, image, src, folder):
    made = []
    for arch in ARCHES:
        tar = folder / f"dexstash-{arch}.tar"
        run(["docker", "buildx", "build", "--builder", BUILDER, "--platform", f"linux/{arch}",
             "--tag", f"{image}:{version}", "--output", f"type=docker,dest={tar}", str(src)])
        name = f"dexstash-{version}-{arch}.tar.gz"
        packed = folder / name
        with open(tar, "rb") as raw, gzip.open(packed, "wb") as zipped:
            shutil.copyfileobj(raw, zipped)
        tar.unlink()
        digest = hashlib.sha256(packed.read_bytes()).hexdigest()
        (folder / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
        made += [str(packed), str(folder / f"{name}.sha256")]
    run(["gh", "release", "upload", tag, *made, "--clobber"], cwd=ROOT)


def registry(version, image, latest, src):
    tags = [version, version.rsplit(".", 1)[0]] + (["latest"] if latest else [])
    cmd = ["docker", "buildx", "build", "--builder", BUILDER, "--platform",
           ",".join(f"linux/{a}" for a in ARCHES), "--push"]
    for t in tags:
        cmd += ["--tag", f"{image}:{t}"]
    run(cmd + [str(src)])
    print("Pushed", ", ".join(f"{image}:{t}" for t in tags))


def main(argv):
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1 or flags - {"--files-only", "--registry-only"}:
        sys.exit(__doc__)
    tag = args[0]
    for tool in ("docker", "gh", "git"):
        if not shutil.which(tool):
            fail(f"{tool} isn't installed.")
    info = release_info(tag)
    image = image_name()
    with tempfile.TemporaryDirectory(prefix="dexstash-release-") as tmp:
        folder = Path(tmp)
        src = checkout(tag, folder)
        version = app_version(src)
        if f"v{version}" != tag:
            fail(f"tag {tag} but app/__init__.py says {version}.")
        builder()
        if "--registry-only" not in flags:
            files(tag, version, image, src, folder)
        if "--files-only" not in flags:
            sign_in()
            registry(version, image, not info["isPrerelease"], src)
    print(f"Done: {tag}.")


if __name__ == "__main__":
    main(sys.argv[1:])
