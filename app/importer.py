"""Importing a whole collection from a Collectr CSV export.

1. upload: the file is read into import_rows and matching starts on its own
   thread (it may load dozens of sets from TCGdex).
2. review: the file's quantities are compared with the collection. Rows that
   didn't match can be fixed by hand (a card, a whole set, or ignore).
3. apply: quantities are set to the file's, the history records who imported,
   purchase prices go into one spending entry, and every match is remembered
   in import_map so the next import of the same file lines up.
"""

import json
import logging
import sqlite3
from datetime import date

from . import catalog, clock, collectr, db, fx, inventory, notes, spending
from .collectr import SOURCE

log = logging.getLogger("dexstash.import")

MAX_BYTES = 5_000_000


# --- reading the file -------------------------------------------------------

def create_batch(conn: sqlite3.Connection, collection_id: int, user_id: int, filename: str, data: bytes) -> int:
    if len(data) > MAX_BYTES:
        raise ValueError("That file is too big for a collection export.")
    rows = collectr.parse(data)  # raises ValueError
    if not rows:
        raise ValueError("The file has no rows.")
    with db.transaction(conn):
        batch_id = conn.execute(
            "INSERT INTO import_batches (collection_id, created_by, filename, source, file_text, status, message) "
            "VALUES (?, ?, ?, ?, ?, 'matching', 'Waiting to start')",
            (collection_id, user_id, filename, SOURCE, data.decode("utf-8-sig", errors="replace")),
        ).lastrowid
        conn.executemany(
            "INSERT INTO import_rows (batch_id, line, key, set_name, raw_json, variant, condition, quantity, cost, "
            "note, status, reason, cost_currency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(batch_id, r.line, r.key, r.set_name, json.dumps(r.raw), r.variant, r.condition, r.quantity, r.cost,
              r.note, "ignored" if r.skip else "unmatched", r.skip, fx.setting()) for r in rows],
        )
    return batch_id


def _row(raw_json: str, line: int) -> collectr.Row:
    """A stored row read back the way the file was read."""
    raw = json.loads(raw_json)
    header = list(raw.keys())
    import csv
    import io
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=header)
    writer.writeheader()
    writer.writerow(raw)
    row = collectr.parse(out.getvalue().encode())[0]
    row.line = line
    return row


# --- matching ---------------------------------------------------------------

class Matcher:
    """Matching an import's rows is a job ('import:<batch>'), one at a time with the rest. A restart stops it
    (stop_left_over); the import page then offers Start matching again."""

    def __init__(self, api_for, jobs=None):
        self.api_for = api_for
        self.jobs = jobs

    def running(self, batch_id: int) -> bool:
        return self.jobs is not None and self.jobs.running(f"import:{batch_id}")

    def start(self, batch_id: int, wait: bool = False) -> None:
        if self.jobs is None:  # on its own (a test)
            conn = db.connect()
            try:
                self._run(conn, batch_id)
            finally:
                conn.close()
            return
        conn = db.connect()
        try:
            conn.execute("UPDATE import_batches SET status = 'matching', message = ? WHERE id = ?",
                         ("Waiting its turn behind other background work", batch_id))
        finally:
            conn.close()
        self.jobs.start(f"import:{batch_id}", "Matching an import's cards", lambda c, job: self._run(c, batch_id),
                        wait=wait)

    def _run(self, conn, batch_id: int) -> None:
        try:
            match_batch(conn, batch_id, self.api_for)
        except Exception as e:
            log.exception("import %s failed", batch_id)
            conn.execute("UPDATE import_batches SET status = 'failed', message = ? WHERE id = ?",
                         (f"Matching stopped: {e}", batch_id))
            conn.commit()


STOPPED = "Matching was stopped by a restart."


def stop_left_over(conn) -> int:
    """At start: an import that was matching stopped with the restart; you start it again on its page."""
    stopped = conn.execute("UPDATE import_batches SET status = 'stopped', message = ? WHERE status = 'matching'", (STOPPED,))
    conn.commit()
    return stopped.rowcount


def _progress(conn, batch_id: int, message: str) -> None:
    conn.execute("UPDATE import_batches SET message = ? WHERE id = ?", (message, batch_id))
    conn.commit()


def _saved(conn, collection_id: int, key: str):
    return conn.execute("SELECT * FROM import_map WHERE collection_id = ? AND source = ? AND key = ?",
                        (collection_id, SOURCE, key)).fetchone()


def set_key(set_name: str) -> str:
    return "set:" + collectr.fold(set_name)


def match_batch(conn: sqlite3.Connection, batch_id: int, api_for) -> None:
    """Match the batch's unmatched rows (rows you matched or ignored stay as they are)."""
    batch = conn.execute("SELECT * FROM import_batches WHERE id = ?", (batch_id,)).fetchone()
    coll = batch["collection_id"]
    conn.execute("UPDATE import_batches SET status = 'matching' WHERE id = ?", (batch_id,))
    _progress(conn, batch_id, "Reading the file")
    todo = conn.execute(
        "SELECT * FROM import_rows WHERE batch_id = ? AND (status = 'unmatched' OR matched_by IN ('auto', 'saved'))",
        (batch_id,)).fetchall()

    en_sets: list[dict] | None = None
    briefs: dict[tuple[str, str], list[dict]] = {}   # (lang, TCGdex set id) -> cards
    failed: dict[tuple[str, str], str] = {}
    results: dict[int, tuple] = {}                    # row id -> (status, lang, tcgdex set, tcgdex card | None, how, reason)

    def cards_of(lang: str, set_id: str) -> list[dict]:
        key = (lang, set_id)
        if key not in briefs and key not in failed:
            local = catalog.local_id(lang, set_id)
            stored = conn.execute("SELECT id, number, name FROM cards WHERE set_id = ?", (local,)).fetchall()
            if stored:
                briefs[key] = [{"id": catalog.tcgdex_set_id(r["id"]) if lang != "en" else r["id"],
                                "number": r["number"], "name": r["name"]} for r in stored]
            else:
                try:
                    data = api_for(lang).get_set(set_id)
                    briefs[key] = [{"id": c["id"], "number": c["localId"], "name": c["name"]}
                                   for c in data.get("cards", [])]
                except Exception as e:
                    failed[key] = str(e)
        return briefs.get(key, [])

    by_set: dict[str, list] = {}
    for r in todo:
        saved = _saved(conn, coll, r["key"])
        if saved is not None:
            if saved["card_id"] is None:
                results[r["id"]] = ("ignored", None, None, None, "saved", "You chose to ignore this row before")
            else:
                lang = saved["lang"] or "en"
                set_id = saved["set_id"] or ""
                card = catalog.tcgdex_set_id(saved["card_id"]) if lang != "en" else saved["card_id"]
                results[r["id"]] = ("matched", lang, set_id, card, "saved", None)
            continue
        by_set.setdefault(r["set_name"], []).append(r)

    for n, (set_name, rows) in enumerate(sorted(by_set.items()), start=1):
        _progress(conn, batch_id, f"Matching {set_name} ({n} of {len(by_set)} sets)")
        parsed = [(r, _row(r["raw_json"], r["line"])) for r in rows]
        mapped = _saved(conn, coll, set_key(set_name))
        if mapped is not None and mapped["set_id"]:
            lang, set_ids, check_name = mapped["lang"] or "en", [mapped["set_id"]], (mapped["lang"] or "en") == "en"
        else:
            lang, check_name = "en", True
            if en_sets is None:
                en_sets = api_for("en").list_sets()
            totals = [p.printed_total for _, p in parsed if p.printed_total]
            set_ids = collectr.resolve_set(set_name, totals[0] if totals else None, en_sets)
        for r, p in parsed:
            if mapped is None and p.language:
                results[r["id"]] = ("unmatched", None, None, None, None,
                                    f"{_LANGUAGE_NAMES.get(p.language, 'Not an English')} card")
                continue
            if not set_ids:
                results[r["id"]] = ("unmatched", None, None, None, None, "Set not found: pick it below")
                continue
            candidates = []
            for set_id in set_ids:
                candidates += [dict(c, set_id=set_id) for c in cards_of(lang, set_id)]
            errors = [failed[(lang, s)] for s in set_ids if (lang, s) in failed]
            if not candidates and errors:
                results[r["id"]] = ("unmatched", None, None, None, None, f"Couldn't load the set: {errors[0]}")
                continue
            card = collectr.pick_card(p, candidates, check_name=check_name)
            if card is None:
                results[r["id"]] = ("unmatched", None, None, None, None, f"No #{p.number} {p.clean_name} in that set")
            else:
                results[r["id"]] = ("matched", lang, card["set_id"], card["id"], "auto", None)

    # Load the card data for every set something matched in.
    needed = sorted({(lang, s) for status, lang, s, *_ in results.values() if status == "matched"})
    for n, (lang, set_id) in enumerate(needed, start=1):
        local = catalog.local_id(lang, set_id)
        if conn.execute("SELECT 1 FROM sets WHERE set_id = ?", (local,)).fetchone():
            continue
        _progress(conn, batch_id, f"Loading card data for set {n} of {len(needed)}")
        try:
            catalog.cache_set(conn, api_for(lang), set_id)
        except Exception as e:
            failed[(lang, set_id)] = str(e)

    with db.transaction(conn):
        for row_id, (status, lang, set_id, card, how, reason) in results.items():
            our_card = catalog.local_id(lang, card) if card else None
            if status == "matched" and (
                    (lang, set_id) in failed
                    or conn.execute("SELECT 1 FROM cards WHERE id = ?", (our_card,)).fetchone() is None):
                status, our_card, how = "unmatched", None, None
                reason = f"Couldn't load the set: {failed.get((lang, set_id), 'card not in the set data')}"
            conn.execute("UPDATE import_rows SET status = ?, card_id = ?, matched_by = ?, reason = ? WHERE id = ?",
                         (status, our_card, how, reason, row_id))
        conn.execute("UPDATE import_batches SET status = 'ready', message = NULL WHERE id = ?", (batch_id,))


_LANGUAGE_NAMES = {"ja": "Japanese", "zh": "Chinese", "ko": "Korean"}


# --- fixing rows by hand ----------------------------------------------------

def get_batch(conn: sqlite3.Connection, collection_id: int, batch_id: int) -> dict | None:
    row = conn.execute(
        "SELECT b.*, u.username AS created_by_name FROM import_batches b LEFT JOIN users u ON u.id = b.created_by "
        "WHERE b.id = ? AND b.collection_id = ?", (batch_id, collection_id)).fetchone()
    return dict(row) if row else None


def get_row(conn: sqlite3.Connection, collection_id: int, row_id: int) -> dict | None:
    row = conn.execute(
        "SELECT r.* FROM import_rows r JOIN import_batches b ON b.id = r.batch_id "
        "WHERE r.id = ? AND b.collection_id = ?", (row_id, collection_id)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["raw"] = json.loads(d["raw_json"])
    return d


def _remember(conn, collection_id: int, key: str, card_id: str | None, set_id: str | None, lang: str | None,
              variant: str | None) -> None:
    conn.execute(
        """INSERT INTO import_map (collection_id, source, key, card_id, set_id, lang, variant, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
           ON CONFLICT(collection_id, source, key) DO UPDATE SET card_id = excluded.card_id,
             set_id = excluded.set_id, lang = excluded.lang, variant = excluded.variant, updated_at = excluded.updated_at""",
        (collection_id, SOURCE, key, card_id, set_id, lang, variant),
    )


def match_row(conn: sqlite3.Connection, collection_id: int, row: dict, card_id: str) -> None:
    """You picked the card for this row (our card id). Remembered for next time,
    for every row in the batch with the same key."""
    card = conn.execute("SELECT c.id, c.set_id, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id "
                        "WHERE c.id = ?", (card_id,)).fetchone()
    with db.transaction(conn):
        conn.execute("UPDATE import_rows SET status = 'matched', card_id = ?, matched_by = 'you', reason = NULL "
                     "WHERE batch_id = ? AND key = ?", (card_id, row["batch_id"], row["key"]))
        _remember(conn, collection_id, row["key"], card_id, catalog.tcgdex_set_id(card["set_id"]),
                  card["language"], row["variant"])


def ignore_row(conn: sqlite3.Connection, collection_id: int, row: dict) -> None:
    with db.transaction(conn):
        conn.execute("UPDATE import_rows SET status = 'ignored', card_id = NULL, matched_by = 'you', "
                     "reason = 'You chose to ignore this row' WHERE batch_id = ? AND key = ?",
                     (row["batch_id"], row["key"]))
        _remember(conn, collection_id, row["key"], None, None, None, None)


def unignore_row(conn: sqlite3.Connection, collection_id: int, row: dict) -> None:
    with db.transaction(conn):
        conn.execute("UPDATE import_rows SET status = 'unmatched', matched_by = NULL, "
                     "reason = 'Find the card, or ignore it again' WHERE batch_id = ? AND key = ?",
                     (row["batch_id"], row["key"]))
        conn.execute("DELETE FROM import_map WHERE collection_id = ? AND source = ? AND key = ?",
                     (collection_id, SOURCE, row["key"]))


def map_set(conn: sqlite3.Connection, collection_id: int, batch_id: int, set_name: str, lang: str,
            set_id: str) -> None:
    """Every row of this file set is from this TCGdex set. Rows are then
    matched by number (and name, for English). Rematch the batch after."""
    with db.transaction(conn):
        _remember(conn, collection_id, set_key(set_name), None, set_id, lang, None)
        conn.execute("UPDATE import_rows SET status = 'unmatched', card_id = NULL, matched_by = NULL, "
                     "reason = 'Matching again' WHERE batch_id = ? AND set_name = ? AND COALESCE(matched_by, '') != 'you' "
                     "AND status != 'ignored'", (batch_id, set_name))
        conn.execute("UPDATE import_batches SET status = 'matching', message = 'Waiting to start' WHERE id = ?",
                     (batch_id,))


# --- review and apply -------------------------------------------------------

def rows(conn: sqlite3.Connection, batch_id: int) -> list[dict]:
    out = []
    for r in conn.execute(
        "SELECT r.*, c.name AS card_name, c.number AS card_number, c.image_url, s.name AS card_set_name, s.logo_url "
        "FROM import_rows r LEFT JOIN cards c ON c.id = r.card_id LEFT JOIN sets s ON s.set_id = c.set_id "
        "WHERE r.batch_id = ? ORDER BY r.set_name, r.line", (batch_id,)):
        d = dict(r)
        raw = json.loads(d.pop("raw_json"))
        d.update(name=raw.get("Product Name", ""), number=raw.get("Card Number", ""),
                 variance=raw.get("Variance", ""), rarity=raw.get("Rarity", ""),
                 variant_label=inventory.VARIANTS.get(d["variant"], d["variant"]),
                 market=raw.get(next((k for k in raw if k.startswith("Market Price")), ""), ""))
        d["search"] = f"{collectr.clean_name(d['name'])} {d['number']}".strip()
        out.append(d)
    return out


def _groups(conn, batch_id: int) -> dict[tuple, dict]:
    """(card_id, variant, condition) -> what the file says: quantity, cost, notes."""
    groups: dict[tuple, dict] = {}
    for r in conn.execute("SELECT * FROM import_rows WHERE batch_id = ? AND status = 'matched'", (batch_id,)):
        g = groups.setdefault((r["card_id"], r["variant"], r["condition"]),
                              {"quantity": 0, "cost_total": 0.0, "costed": 0, "notes": []})
        g["quantity"] += r["quantity"]
        if r["cost"]:
            g["cost_total"] += r["cost"] * r["quantity"]
            g["costed"] += r["quantity"]
        if r["note"] and r["note"] not in g["notes"]:
            g["notes"].append(r["note"])
    return groups


def review(conn: sqlite3.Connection, collection_id: int, batch_id: int) -> dict:
    """How the file compares with the collection right now."""
    groups = _groups(conn, batch_id)
    have = {(r["card_id"], r["variant"], r["condition"]): r["quantity"] for r in conn.execute(
        "SELECT card_id, variant, condition, quantity FROM inventory WHERE user_id = ?", (collection_id,))}
    info = {r["id"]: dict(r) for r in conn.execute(
        "SELECT c.id, c.name, c.number, s.name AS set_name FROM cards c JOIN sets s ON s.set_id = c.set_id "
        "WHERE c.id IN (SELECT card_id FROM import_rows WHERE batch_id = ? AND card_id IS NOT NULL "
        "UNION SELECT card_id FROM inventory WHERE user_id = ?)", (batch_id, collection_id))}
    changes = {"new": [], "more": [], "fewer": [], "same": [], "missing": []}
    for key, g in groups.items():
        current = have.get(key, 0)
        kind = "new" if current == 0 else "more" if g["quantity"] > current else \
            "fewer" if g["quantity"] < current else "same"
        changes[kind].append(_change(key, info, current, g["quantity"]))
    for key, current in have.items():
        if key not in groups:
            changes["missing"].append(_change(key, info, current, 0))
    for items in changes.values():
        items.sort(key=lambda c: (c["set_name"], c["name"]))
    added = [(key, g, g["quantity"] - have.get(key, 0)) for key, g in groups.items()]
    costed = [(key, g, min(delta, g["costed"])) for key, g, delta in added if delta > 0 and g["costed"]]
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM import_rows WHERE batch_id = ? GROUP BY status",
                               (batch_id,)).fetchall())
    return {
        "changes": changes,
        "counts": {k: counts.get(k, 0) for k in ("matched", "unmatched", "ignored")},
        "spend_cards": sum(q for _, _, q in costed),
        "spend_total": round(sum(g["cost_total"] / g["costed"] * q for _, g, q in costed), 2),
        "currency": (conn.execute("SELECT cost_currency FROM import_rows WHERE batch_id = ? LIMIT 1", (batch_id,)).fetchone()
                     or ["USD"])[0],
    }


def _change(key, info, current, target):
    card = info.get(key[0], {"name": key[0], "number": "", "set_name": ""})
    return {"card_id": key[0], "variant": key[1], "variant_label": inventory.VARIANTS.get(key[1], key[1]),
            "condition": key[2], "name": card["name"], "number": card["number"], "set_name": card["set_name"],
            "current": current, "target": target}


def apply(conn: sqlite3.Connection, collection_id: int, batch_id: int, user_id: int, lower: bool = True,
          remove_missing: bool = False) -> dict:
    """Make the collection match the file. Returns what changed."""
    batch = get_batch(conn, collection_id, batch_id)
    if batch is None or batch["status"] != "ready":
        raise ValueError("This import isn't ready to apply.")
    groups = _groups(conn, batch_id)
    have = {(r["card_id"], r["variant"], r["condition"]): r["quantity"] for r in conn.execute(
        "SELECT card_id, variant, condition, quantity FROM inventory WHERE user_id = ?", (collection_id,))}
    done = {"added": 0, "removed": 0, "cards_changed": 0, "notes": 0, "purchase_id": None, "added_lines": []}
    pulls = []
    with db.transaction(conn):
        for key, g in groups.items():
            current = have.get(key, 0)
            target = g["quantity"]
            if target < current and not lower:
                target = current
            if target != current:
                inventory.set_quantity(conn, collection_id, *key, target, by=user_id, source="import")
                done["cards_changed"] += 1
                done["added" if target > current else "removed"] += abs(target - current)
                if target > current:
                    done["added_lines"].append((key, target - current))
            if target > current and g["costed"]:
                quantity = min(target - current, g["costed"])
                pulls.append((key, quantity, round(g["cost_total"] / g["costed"], 2)))
            for text in g["notes"]:
                if not notes.exists(conn, collection_id, key[0], text):
                    notes.add(conn, collection_id, key[0], user_id, text, key[1], key[2], commit=False)
                    done["notes"] += 1
        if remove_missing:
            for key, current in have.items():
                if key not in groups:
                    inventory.set_quantity(conn, collection_id, *key, 0, by=user_id, source="import")
                    done["cards_changed"] += 1
                    done["removed"] += current
        if pulls:
            total = sum(q * cost for _, q, cost in pulls)
            bought_in = conn.execute("SELECT cost_currency FROM import_rows WHERE batch_id = ? LIMIT 1",
                                     (batch_id,)).fetchone()  # The file's costs, in the currency at import
            purchase_id = spending.create_purchase(
                conn, collection_id, f"Import {date.today().isoformat()}", total,
                purchased_on=date.today().isoformat(), by=user_id, commit=False,
                currency=bought_in[0] if bought_in else None)
            conn.executemany(
                "INSERT INTO pulls (purchase_id, card_id, variant, condition, quantity, cost_each) VALUES (?, ?, ?, ?, ?, ?)",
                [(purchase_id, *key, q, cost) for key, q, cost in pulls])
            done["purchase_id"] = purchase_id
        for r in conn.execute("SELECT r.key, r.card_id, r.variant, c.set_id, s.language FROM import_rows r "
                              "JOIN cards c ON c.id = r.card_id JOIN sets s ON s.set_id = c.set_id "
                              "WHERE r.batch_id = ? AND r.status = 'matched'", (batch_id,)).fetchall():
            _remember(conn, collection_id, r["key"], r["card_id"], catalog.tcgdex_set_id(r["set_id"]),
                      r["language"], r["variant"])
        conn.execute("UPDATE import_batches SET status = 'applied', applied_at = ?, purchase_id = ?, message = ? "
                     "WHERE id = ?", (clock.now(), done["purchase_id"],
                                      json.dumps(done), batch_id))
    return done


def again(conn: sqlite3.Connection, collection_id: int, batch: dict, user_id: int) -> int:
    """A new import of the same file, so rows fixed after applying can go in.
    Remembered matches make it line up, so only differences change."""
    return create_batch(conn, collection_id, user_id, batch["filename"], (batch["file_text"] or "").encode())


def batches(conn: sqlite3.Connection, collection_id: int) -> list[dict]:
    out = []
    for b in conn.execute(
        "SELECT b.id, b.filename, b.status, b.message, b.created_at, b.applied_at, b.purchase_id, "
        "u.username AS created_by_name, "
        "(SELECT COUNT(*) FROM import_rows WHERE batch_id = b.id) AS rows, "
        "(SELECT COUNT(*) FROM import_rows WHERE batch_id = b.id AND status = 'unmatched') AS unmatched "
        "FROM import_batches b LEFT JOIN users u ON u.id = b.created_by WHERE b.collection_id = ? "
        "ORDER BY b.id DESC", (collection_id,)):
        out.append(dict(b))
    return out


def delete_batch(conn: sqlite3.Connection, collection_id: int, batch_id: int) -> None:
    """Forget an import (not what it changed; remembered matches stay too)."""
    with db.transaction(conn):
        if conn.execute("SELECT 1 FROM import_batches WHERE id = ? AND collection_id = ?",
                        (batch_id, collection_id)).fetchone():
            conn.execute("DELETE FROM import_rows WHERE batch_id = ?", (batch_id,))
            conn.execute("DELETE FROM import_batches WHERE id = ?", (batch_id,))


def unmatched_sets(batch_rows: list[dict]) -> list[dict]:
    """Unmatched rows grouped by the file's set name, for fixing a set at once."""
    groups: dict[str, dict] = {}
    for r in batch_rows:
        if r["status"] != "unmatched":
            continue
        g = groups.setdefault(r["set_name"], {"set_name": r["set_name"], "rows": [], "lang": "en"})
        g["rows"].append(r)
        for code, name in _LANGUAGE_NAMES.items():  # the CSV said what language the card is in
            if (r["reason"] or "").startswith(name):
                g["lang"], g["language_name"] = code, name
    return sorted(groups.values(), key=lambda g: -len(g["rows"]))


# --- clearing ---------------------------------------------------------------

def clear_collection(conn: sqlite3.Connection, collection_id: int, forget_matches: bool = False) -> None:
    """Empty the collection: cards, wants, spending, notes, history and imports.
    Accounts and the cached card data stay."""
    with db.transaction(conn):
        conn.execute("DELETE FROM pulls WHERE purchase_id IN (SELECT id FROM purchases WHERE user_id = ?)",
                     (collection_id,))
        for table, column in (("purchases", "user_id"), ("inventory", "user_id"), ("wants", "user_id"),
                              ("notes", "collection_id"), ("inventory_log", "collection_id")):
            conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (collection_id,))
        conn.execute("DELETE FROM import_rows WHERE batch_id IN (SELECT id FROM import_batches WHERE collection_id = ?)",
                     (collection_id,))
        conn.execute("DELETE FROM import_batches WHERE collection_id = ?", (collection_id,))
        if forget_matches:
            conn.execute("DELETE FROM import_map WHERE collection_id = ?", (collection_id,))
