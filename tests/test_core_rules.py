"""The core rules (rules/core.md) that tests can hold: the rules check script itself, and the sites dexSTASH
must never read."""

import importlib.util
import re
from pathlib import Path

import pytest

from app import sites

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_rules", ROOT / "tools" / "check_rules.py")
check_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_rules)


# --- Link out, don't scrape -------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.tcgplayer.com/product/12345",
    "https://tcgplayer.com/search/pokemon/product?q=pikachu",
    "https://infinite-api.tcgplayer.com/price/history/1",
    "https://www.pricecharting.com/game/pokemon-base-set/charizard-4",
    "https://pricecharting.com/search-products?q=pikachu",
    "https://storage.googleapis.com/images.pricecharting.com/abc/240.jpg",
])
def test_pages_on_tcgplayer_and_pricecharting_are_never_read(url):
    assert sites.blocked(url)


@pytest.mark.parametrize("url", [
    "https://tcgplayer-cdn.tcgplayer.com/product/12345_200w.jpg",  # product pictures, listed by TCGCSV
    "https://www.pricecharting.com/api/product?t=token&id=1",  # the official API, with the host's own token
    "https://api.tcgdex.net/v2/en/cards/sv03-223",
])
def test_pictures_and_official_apis_are_allowed(url):
    assert not sites.blocked(url)


# --- The rules check ----------------------------------------------------------------------------------------------

def _problems(text, words=()):
    return check_rules.check_text("f.md", text, [re.compile(w, re.I) for w in words])


@pytest.mark.parametrize("line", [
    "cd /volume1/docker/dexstash",  # rules: allow
    "open /Users/somebody/sandbox/dexSTASH",  # rules: allow
    r"C:\Users\somebody\sandbox",  # rules: allow
    "copy it to /home/somebody/apps",  # rules: allow
    "write to someone@gmail.com",  # rules: allow
    "served at https://my-cards.duckdns.org",  # rules: allow
])
def test_personal_details_are_found(line):
    assert _problems(line), line


@pytest.mark.parametrize("line", [
    "cd /srv/docker/dexstash",
    "your-name.duckdns.org and cards.example.com",
    "misty@example.com or 1+someone@users.noreply.github.com",
    "Committed by GitHub <noreply@github.com>",
    "Co-Authored-By: Claude <noreply@anthropic.com>",
    "a path like /home/user/dexstash in the docs",
    "cd /volume1/docker  # rules: allow",
])
def test_examples_and_marked_lines_pass(line):
    assert not _problems(line), line


def test_private_words_are_reported_without_being_printed():
    found = _problems("hosted on Rainbowbox at home", [r"(?<![\w-])rainbowbox(?![\w-])"])
    assert len(found) == 1 and "Rainbowbox" not in found[0] and "private list" in found[0]
    assert not _problems("a rainbowboxes word", [r"(?<![\w-])rainbowbox(?![\w-])"])


def test_private_words_come_from_the_environment_and_the_list_file(tmp_path, monkeypatch):
    monkeypatch.setattr(check_rules, "ROOT", tmp_path)
    monkeypatch.setenv("DEXSTASH_PRIVATE_WORDS", " Rainbowbox , ,Other Name ")
    words = check_rules.private_words()
    assert len(words) == 2 and words[1].search("see other name here")
    monkeypatch.delenv("DEXSTASH_PRIVATE_WORDS")
    assert check_rules.private_words() == []
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "private-words.txt").write_text("# a comment\nWaterflower\n\nthird word\n")
    words = check_rules.private_words()
    assert len(words) == 2 and words[0].search("Misty WATERFLOWER") and words[1].search("a third word")


@pytest.mark.parametrize("path", [".env", "deploy/.env", "data/dexstash.db", "x.sqlite3-wal", "app/models/clip.onnx",
                                  "dist/dexstash.tar.gz", "old.bundle"])
def test_files_that_must_never_be_committed_are_found(path):
    assert check_rules.check_names([path])


def test_ordinary_files_pass_the_name_check():
    assert not check_rules.check_names([".env.example", "app/set_codes.json", "app/schema.sql", "docs/data-model.md"])


def test_the_repository_passes_its_own_check():
    assert check_rules.check_files(check_rules.tracked_files(), []) == []


def _repo_with_commit(tmp_path, monkeypatch, name, email, message):
    import subprocess
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name,
           "GIT_COMMITTER_EMAIL": email, "HOME": str(tmp_path), "PATH": __import__("os").environ["PATH"]}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty", "-m", message], check=True, env=env)
    monkeypatch.setattr(check_rules, "ROOT", tmp_path)


def test_a_commit_with_a_personal_email_or_a_private_link_is_found(tmp_path, monkeypatch):
    _repo_with_commit(tmp_path, monkeypatch, "Misty", "misty@" + "gmail.com",
                      "Add a thing\n\nSee https://claude.ai/code/" + "session_0123")
    problems = check_rules.check_history([])
    assert any("an e-mail address" in p for p in problems)
    assert any("a link to a private page" in p for p in problems)


@pytest.mark.parametrize("line", ["Co-Authored-By: Claude <noreply@anthropic.com>",
                                  "Generated with [Claude Code](https://claude.com/claude-code)"])
def test_an_attribution_line_in_a_commit_is_found(tmp_path, monkeypatch, line):
    _repo_with_commit(tmp_path, monkeypatch, "misty", "1+misty@users.noreply.github.com", f"Add a thing\n\n{line}")
    assert any("an attribution line" in p for p in check_rules.check_history([]))


def test_a_commit_with_noreply_addresses_passes(tmp_path, monkeypatch):
    _repo_with_commit(tmp_path, monkeypatch, "misty", "1+misty@users.noreply.github.com",
                      "Add a thing\n\n- New check for commits\n\n#12")
    assert check_rules.check_history([]) == []


def test_a_private_word_in_a_commit_is_found(tmp_path, monkeypatch):
    _repo_with_commit(tmp_path, monkeypatch, "Misty Waterflower", "1+m@users.noreply.github.com", "Add a thing")
    assert check_rules.check_history([re.compile("waterflower", re.I)])
