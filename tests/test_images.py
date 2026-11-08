"""Images built on GitHub (issues #2 and #4): a test image per pull request, image files on each release, and
running a ready-built image from .env without editing docker-compose.yml."""

from pathlib import Path

import yaml

from app import settings

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _build_step(job: dict) -> dict:
    return next(s for s in job["steps"] if s.get("uses", "").startswith("docker/build-push-action"))


def test_every_pull_request_gets_a_test_image_for_both_platforms():
    flow = _yaml(WORKFLOWS / "pr-image.yml")
    assert flow[True] == {"pull_request": {"types": ["opened", "synchronize", "reopened", "closed"]}}  # yaml reads "on" as True
    job = flow["jobs"]["image"]
    meta = next(s for s in job["steps"] if s.get("id") == "meta")
    assert meta["with"]["images"] == "ghcr.io/${{ github.repository_owner }}/dexstash"
    assert meta["with"]["tags"].startswith("type=ref,event=pr")  # tagged pr-<number>
    build = _build_step(job)
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert build["with"]["push"] is True and build["with"]["tags"] == "${{ steps.meta.outputs.tags }}"


def test_forks_publish_nothing():
    jobs = _yaml(WORKFLOWS / "pr-image.yml")["jobs"]
    same_repo = "github.event.pull_request.head.repo.full_name == github.repository"
    assert same_repo in jobs["image"]["if"] and same_repo in jobs["remove"]["if"]


def test_a_closed_pull_request_s_image_is_removed():
    jobs = _yaml(WORKFLOWS / "pr-image.yml")["jobs"]
    assert "github.event.action != 'closed'" in jobs["image"]["if"]
    assert "github.event.action == 'closed'" in jobs["remove"]["if"]
    script = jobs["remove"]["steps"][0]["run"]
    assert "packages/container/dexstash/versions" in script and "-X DELETE" in script
    assert jobs["remove"]["steps"][0]["env"]["TAG"] == "pr-${{ github.event.pull_request.number }}"


def test_a_published_release_gets_image_files_after_the_tests():
    flow = _yaml(WORKFLOWS / "image.yml")
    assert flow[True] == {"release": {"types": ["published"]}}  # not a registry: files on the Release
    assert flow["permissions"] == {"contents": "write"}
    job = flow["jobs"]["image"]
    assert job["needs"] == "tests" and job["strategy"]["matrix"]["arch"] == ["amd64", "arm64"]
    build = _build_step(job)
    assert build["with"]["platforms"] == "linux/${{ matrix.arch }}" and "push" not in build["with"]
    assert build["with"]["outputs"] == "type=docker,dest=dexstash.tar"
    upload = job["steps"][-1]["run"]
    assert 'gh release upload "$TAG"' in upload and "-${{ matrix.arch }}.tar.gz" in upload
    assert "ghcr.io" not in (WORKFLOWS / "image.yml").read_text(encoding="utf-8")


def test_compose_builds_here_unless_env_names_an_image():
    services = _yaml(ROOT / "docker-compose.yml")["services"]
    for name in ("app", "scanner"):
        assert services[name]["build"] == "."
        assert services[name]["image"] == "${DEXSTASH_IMAGE:-dexstash:latest}"
        assert services[name]["pull_policy"] == "${DEXSTASH_PULL_POLICY:-build}"
    assert {"IMAGE", "PULL_POLICY"} <= set(settings.KNOWN)
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "# DEXSTASH_IMAGE=dexstash:" in example and "# DEXSTASH_PULL_POLICY=never" in example


def test_deploy_explains_published_and_test_images():
    deploy = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")
    for heading in ("### Using a release image", "### Trying a pull request"):
        assert heading in deploy
    assert "docker load -i dexstash-" in deploy and "DEXSTASH_PULL_POLICY=never" in deploy
    assert "read:packages" in deploy and "docker login ghcr.io" in deploy  # pull request images stay in Packages
