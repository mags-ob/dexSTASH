"""Watches GitHub for a pull request with the test label and runs it in the test copy.

Every few minutes it asks GitHub's public API (no token) which open pull requests carry the label. The one
labelled most recently is built on this server with try-pr.sh, and when that's a different pull request from
the one running, the test data is refreshed from live with reset-test-data.sh. It remembers what it last
tried in .watch-state.json, so an unchanged pull request (or one whose build failed) isn't built again until
it gets a new commit. Only branches in the repository itself are built, never forks.
"""
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE / ".watch-state.json"
SELF = "dexstash-test-watcher"


def log(message):
    """Prints one line for the container's log, with the time."""
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def get_json(url):
    """Gives back GitHub's reply to a GET as (data, Link header)."""
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "dexstash-test-watcher"})
    with urllib.request.urlopen(request, timeout=30) as reply:
        return json.load(reply), reply.headers.get("Link", "")


def labelled_prs(get, repo, label):
    """Gives back the numbers of open pull requests with the label."""
    items, _ = get(f"{API}/repos/{repo}/issues?state=open&labels={urllib.parse.quote(label)}&per_page=100")
    return [item["number"] for item in items if "pull_request" in item]


def labelled_at(get, repo, number, label):
    """Gives back when the label was last added to a pull request (ISO time, '' if never)."""
    url = f"{API}/repos/{repo}/issues/{number}/events?per_page=100"
    events, link = get(url)
    last = re.search(r'<([^>]+)>;\s*rel="last"', link)
    if last:  # events come oldest first: the newest labelling is on the last page
        events, _ = get(last.group(1))
    times = [e["created_at"] for e in events
             if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == label]
    return max(times, default="")


def pick(get, repo, label):
    """Gives back the pull request to test: (number, head commit, from this repo?) or None."""
    numbers = labelled_prs(get, repo, label)
    if not numbers:
        return None
    if len(numbers) > 1:
        numbers.sort(key=lambda n: labelled_at(get, repo, n, label), reverse=True)
    pr, _ = get(f"{API}/repos/{repo}/pulls/{numbers[0]}")
    head_repo = (pr["head"].get("repo") or {}).get("full_name", "")
    return numbers[0], pr["head"]["sha"], head_repo.lower() == repo.lower()


def check(get, run, state, repo, label):
    """Does one round of watching and gives back the new state (a dict, saved between rounds)."""
    chosen = pick(get, repo, label)
    if chosen is None:
        return state
    number, sha, own = chosen
    if state.get("pr") == number and state.get("sha") == sha:
        return state  # already tried this commit, whether it worked or not
    new = {"pr": number, "sha": sha, "running": state.get("running")}
    if not own:
        log(f"Pull request {number} comes from a fork; only branches in {repo} are built.")
        return new | {"result": "skipped: fork"}
    log(f"Building pull request {number} at {sha[:7]} ...")
    if not run(["./try-pr.sh", str(number), sha]):
        log(f"The build of pull request {number} failed; the test copy keeps what it had. "
            "It tries again when the pull request gets a new commit.")
        return new | {"result": "failed"}
    new["running"] = number
    if state.get("running") != number:
        log("A different pull request: copying fresh data from live ...")
        if not run(["./reset-test-data.sh"]):
            log("Copying fresh data failed; run reset-test-data.sh by hand.")
            return new | {"result": "built, data copy failed"}
    log(f"The test copy now runs pull request {number} at {sha[:7]}.")
    return new | {"result": "ok"}


def host_folder():
    """Gives back this folder's path on the server, which docker compose needs for the test copy's data."""
    out = subprocess.run(
        ["docker", "inspect", "--format",
         '{{range .Mounts}}{{if eq .Destination "/work"}}{{.Source}}{{end}}{{end}}', SELF],
        capture_output=True, text=True, check=False)
    return out.stdout.strip()


def run_script(command):
    """Runs a script in this folder and gives back whether it worked; its output goes to the log."""
    return subprocess.run(command, cwd=HERE, check=False).returncode == 0


def load_state():
    """Gives back what was last tried, or an empty dict."""
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def main():
    """Watches until the container stops."""
    repo = os.environ.get("DEXSTASH_REPO", "mags-ob/dexSTASH")
    label = os.environ.get("TEST_LABEL", "test")
    minutes = max(1, int(os.environ.get("CHECK_MINUTES", "5")))
    folder = host_folder()
    if not folder:
        log(f"Couldn't find this folder's path on the server (is the container named {SELF}?). Stopping.")
        raise SystemExit(1)
    os.environ["DEXSTASH_TEST_DIR"] = folder
    log(f"Watching {repo} for open pull requests labelled '{label}', every {minutes} minutes.")
    state = load_state()
    while True:
        try:
            new = check(get_json, run_script, state, repo, label)
            if new != state:
                STATE.write_text(json.dumps(new))
                state = new
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            log(f"Couldn't check GitHub ({error}); trying again next round.")
        time.sleep(minutes * 60)


if __name__ == "__main__":
    main()
