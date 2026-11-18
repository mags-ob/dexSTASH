"""The test copy's watcher (deploy/test-copy): which pull request it builds, and when. No network."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FOLDER = ROOT / "deploy" / "test-copy"
REPO = "owner/dexSTASH"

spec = importlib.util.spec_from_file_location("watch_prs", FOLDER / "watch_prs.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class FakeGitHub:
    """Answers the watcher's GET requests from a few pull requests."""

    def __init__(self, prs):
        self.prs = prs  # {number: {"sha", "labelled", "fork", "label"}}
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        path = url.removeprefix(watch.API)
        if path.startswith(f"/repos/{REPO}/issues?"):
            items = [{"number": n, "pull_request": {}} for n, pr in self.prs.items() if pr.get("label", True)]
            return items + [{"number": 999}], ""  # an issue with the label is ignored
        if path.endswith("/events?per_page=100"):
            n = int(path.split("/")[-2])
            events = [{"event": "labeled", "label": {"name": "test"}, "created_at": self.prs[n]["labelled"]},
                      {"event": "labeled", "label": {"name": "bug"}, "created_at": "2099-01-01T00:00:00Z"}]
            return events, ""
        n = int(path.rsplit("/", 1)[1])
        head_repo = "someone/dexSTASH" if self.prs[n].get("fork") else REPO.upper()
        return {"head": {"sha": self.prs[n]["sha"], "repo": {"full_name": head_repo}}}, ""


class FakeRunner:
    """Records the scripts the watcher runs; fails the ones listed."""

    def __init__(self, fail=()):
        self.ran, self.fail = [], set(fail)

    def __call__(self, command):
        self.ran.append(command)
        return command[0] not in self.fail


def check(prs, state=None, fail=()):
    run = FakeRunner(fail)
    new = watch.check(FakeGitHub(prs), run, state or {}, REPO, "test")
    return new, run.ran


def test_a_labelled_pull_request_is_built_and_gets_fresh_data():
    new, ran = check({70: {"sha": "a" * 40}})
    assert ran == [["./try-pr.sh", "70", "a" * 40], ["./reset-test-data.sh"]]
    assert new == {"pr": 70, "sha": "a" * 40, "running": 70, "result": "ok"}


def test_nothing_labelled_does_nothing():
    state = {"pr": 70, "sha": "a" * 40, "running": 70, "result": "ok"}
    new, ran = check({70: {"sha": "a" * 40, "label": False}}, state)
    assert ran == [] and new == state


def test_an_unchanged_pull_request_is_not_built_again():
    state = {"pr": 70, "sha": "a" * 40, "running": 70, "result": "ok"}
    new, ran = check({70: {"sha": "a" * 40}}, state)
    assert ran == [] and new == state


def test_a_new_commit_is_built_without_copying_data():
    state = {"pr": 70, "sha": "a" * 40, "running": 70, "result": "ok"}
    new, ran = check({70: {"sha": "b" * 40}}, state)
    assert ran == [["./try-pr.sh", "70", "b" * 40]]
    assert new["sha"] == "b" * 40 and new["result"] == "ok"


def test_a_different_pull_request_gets_fresh_data():
    state = {"pr": 70, "sha": "a" * 40, "running": 70, "result": "ok"}
    new, ran = check({71: {"sha": "c" * 40}}, state)
    assert ran == [["./try-pr.sh", "71", "c" * 40], ["./reset-test-data.sh"]]
    assert new["running"] == 71


def test_the_pull_request_labelled_last_wins():
    prs = {70: {"sha": "a" * 40, "labelled": "2026-11-18T10:00:00Z"},
           71: {"sha": "b" * 40, "labelled": "2026-11-18T12:00:00Z"},
           72: {"sha": "c" * 40, "labelled": "2026-11-18T11:00:00Z"}}
    new, ran = check(prs)
    assert new["pr"] == 71 and ran[0] == ["./try-pr.sh", "71", "b" * 40]


def test_events_are_read_from_the_last_page():
    pages = {"first": ([{"event": "labeled", "label": {"name": "test"}, "created_at": "2026-01-01T00:00:00Z"}],
                       '<https://x/page2>; rel="next", <https://x/page9>; rel="last"'),
             "https://x/page9": ([{"event": "labeled", "label": {"name": "test"},
                                   "created_at": "2026-11-01T00:00:00Z"}], "")}
    seen = lambda url: pages.get(url, pages["first"])  # noqa: E731
    assert watch.labelled_at(seen, REPO, 70, "test") == "2026-11-01T00:00:00Z"


def test_a_fork_is_never_built():
    new, ran = check({70: {"sha": "a" * 40, "fork": True}})
    assert ran == [] and new["result"] == "skipped: fork"
    again, ran = check({70: {"sha": "a" * 40, "fork": True}}, new)
    assert ran == [] and again == new


def test_a_failed_build_is_not_retried_until_a_new_commit():
    state = {"pr": 70, "sha": "a" * 40, "running": 70, "result": "ok"}
    new, ran = check({71: {"sha": "c" * 40}}, state, fail={"./try-pr.sh"})
    assert ran == [["./try-pr.sh", "71", "c" * 40]]  # no data copy: the old build still runs
    assert new["result"] == "failed" and new["running"] == 70
    again, ran = check({71: {"sha": "c" * 40}}, new, fail={"./try-pr.sh"})
    assert ran == [] and again == new
    fixed, ran = check({71: {"sha": "d" * 40}}, new)
    assert ran == [["./try-pr.sh", "71", "d" * 40], ["./reset-test-data.sh"]]
    assert fixed["running"] == 71


def test_a_failed_data_copy_is_reported():
    new, _ = check({70: {"sha": "a" * 40}}, fail={"./reset-test-data.sh"})
    assert new["result"] == "built, data copy failed" and new["running"] == 70


@pytest.mark.parametrize("name", ["try-pr.sh", "reset-test-data.sh"])
def test_the_scripts_only_touch_the_test_app(name):
    # The watcher runs these from inside the same compose project; restarting every service would stop it.
    text = (FOLDER / name).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.startswith("docker compose")]
    assert lines and all(line.endswith(" app") for line in lines)
    assert "docker push" not in text


def test_the_test_image_is_local_and_the_data_path_is_the_servers():
    compose = (FOLDER / "docker-compose.yml").read_text(encoding="utf-8")
    assert "image: dexstash:test" in compose and "ghcr.io" not in compose
    assert "${DEXSTASH_TEST_DIR:-.}/data:/srv/data" in compose
    assert f"container_name: {watch.SELF}" in compose
