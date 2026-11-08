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


def test_a_labelled_pull_request_gets_a_test_image_for_both_platforms():
    flow = _yaml(WORKFLOWS / "pr-image.yml")
    assert flow[True] == {"pull_request": {"types": ["opened", "synchronize", "reopened", "labeled", "closed"]}}  # yaml reads "on" as True
    job = flow["jobs"]["image"]
    assert "contains(github.event.pull_request.labels.*.name, 'test image')" in job["if"]  # issue #49
    assert "github.event.label.name == 'test image'" in job["if"]  # some other label builds nothing
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
    assert flow[True]["release"] == {"types": ["published"]}
    assert flow["permissions"] == {"contents": "write"}
    job = flow["jobs"]["image"]
    assert job["strategy"]["matrix"]["arch"] == ["amd64", "arm64"]
    build = _build_step(job)
    assert build["with"]["platforms"] == "linux/${{ matrix.arch }}" and "push" not in build["with"]
    assert build["with"]["outputs"] == "type=docker,dest=dexstash.tar"
    upload = job["steps"][-1]["run"]
    assert 'gh release upload "$TAG"' in upload and "-${{ matrix.arch }}.tar.gz" in upload
    assert set(job["needs"]) == {"tests", "version"}


def test_a_published_release_is_also_pushed_to_the_registry():
    flow = _yaml(WORKFLOWS / "image.yml")
    job = flow["jobs"]["registry"]
    assert set(job["needs"]) == {"tests", "version"} and job["permissions"]["packages"] == "write"
    meta = next(s for s in job["steps"] if s.get("id") == "meta")
    tags = meta["with"]["tags"]
    assert "needs.version.outputs.version" in tags and "needs.version.outputs.minor" in tags
    assert "value=latest,enable=${{ env.LATEST }}" in tags
    assert flow["env"]["LATEST"] == "${{ !github.event.release.prerelease }}"  # no latest for a pre-release
    build = _build_step(job)
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64" and build["with"]["push"] is True


def test_an_existing_release_can_be_built_again_by_hand():
    flow = _yaml(WORKFLOWS / "image.yml")
    assert flow[True]["workflow_dispatch"]["inputs"]["tag"]["required"] is True
    assert flow["env"]["TAG"] == "${{ github.event.release.tag_name || inputs.tag }}"
    for name in ("version", "image", "registry"):
        checkout = flow["jobs"][name]["steps"][0]
        assert checkout["with"]["ref"] == "${{ env.TAG }}"


def test_compose_uses_the_release_image_and_can_build_it():
    services = _yaml(ROOT / "docker-compose.yml")["services"]
    for name in ("app", "scanner"):
        assert services[name]["image"] == "ghcr.io/obiehq/dexstash:${DEXSTASH_VERSION:-latest}"
        assert services[name]["build"] == "."   # docker compose up -d --build
        assert "pull_policy" not in services[name]
        assert services[name]["env_file"] == [{"path": ".env", "required": False}]   # starts without one
    assert "VERSION" in settings.KNOWN and not {"IMAGE", "PULL_POLICY"} & set(settings.KNOWN)
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "# DEXSTASH_VERSION=" in example


def test_a_loaded_image_file_matches_the_compose_file():
    job = _yaml(ROOT / ".github/workflows/image.yml")["jobs"]["image"]
    build = next(s for s in job["steps"] if s.get("uses", "").startswith("docker/build-push-action"))
    assert build["with"]["tags"] == "${{ steps.name.outputs.image }}:${{ needs.version.outputs.version }}"
    assert any("ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/dexstash" in s.get("run", "") for s in job["steps"])


def test_deploy_explains_published_and_test_images():
    deploy = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")
    for heading in ("### Using a release image", "### Trying a pull request"):
        assert heading in deploy
    assert "docker load -i dexstash-" in deploy and "DEXSTASH_VERSION=0.28.0" in deploy
    assert "DEXSTASH_VERSION=pr-<number>" in deploy and "docker compose pull" in deploy
    assert "`test image` label" in deploy
    assert "raw.githubusercontent.com/obiehq/dexSTASH/main/.env.example" in deploy
    assert "DEXSTASH_IMAGE" not in deploy and "DEXSTASH_PULL_POLICY" not in deploy
