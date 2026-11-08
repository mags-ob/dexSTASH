"""Accounts and passwords. Uses the standard library's scrypt, so no extra
dependency is needed for hashing.

Everyone added by the admin shares the admin's collection (users.collection_id).

Locked out? Reset a password from the command line:
    python -m app.auth reset-password <username>
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=_N, r=_R, p=_P)
    return hmac.compare_digest(digest.hex(), digest_hex)


def user_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


MIN_PASSWORD = 8


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters.")


def create_user(conn: sqlite3.Connection, username: str, password: str, is_admin: bool = False,
                collection_id: int | None = None) -> int:
    """A new account. Without collection_id it gets a collection of its own
    (the first account); otherwise it shares that collection."""
    username = username.strip()
    if not username:
        raise ValueError("Pick a username.")
    _check_password(password)
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        raise ValueError(f"There's already an account called {username}.")
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, is_admin, collection_id, session_key) VALUES (?, ?, ?, ?, ?)",
        (username, hash_password(password), int(is_admin), collection_id, secrets.token_hex(16)),
    )
    if collection_id is None:
        conn.execute("UPDATE users SET collection_id = id WHERE id = ?", (cur.lastrowid,))
    conn.commit()
    return cur.lastrowid


_DUMMY_HASH = hash_password(secrets.token_hex(16))


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
    # An unknown username is checked against a made-up password, so it takes as long as a real one.
    ok = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)
    return row if row and ok else None


def set_password(conn: sqlite3.Connection, user_id: int, password: str) -> str:
    """Change a password. Every other device signed in to the account is signed
    out, because the session key changes. Returns the new key."""
    _check_password(password)
    key = secrets.token_hex(16)
    conn.execute("UPDATE users SET password_hash = ?, session_key = ? WHERE id = ?",
                 (hash_password(password), key, user_id))
    conn.commit()
    return key


def session_key(conn: sqlite3.Connection, user_id: int) -> str:
    """The account's session key, made on first use for accounts from before keys."""
    row = conn.execute("SELECT session_key FROM users WHERE id = ?", (user_id,)).fetchone()
    if row["session_key"]:
        return row["session_key"]
    key = secrets.token_hex(16)
    conn.execute("UPDATE users SET session_key = ? WHERE id = ?", (key, user_id))
    conn.commit()
    return key


def new_session_key(conn: sqlite3.Connection, user_id: int) -> str:
    """Sign out everywhere: every device signed in to the account has to sign in again."""
    key = secrets.token_hex(16)
    conn.execute("UPDATE users SET session_key = ? WHERE id = ?", (key, user_id))
    conn.commit()
    return key


def list_users(conn: sqlite3.Connection, collection_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, username, is_admin, created_at FROM users WHERE collection_id = ? ORDER BY id", (collection_id,))]


def remove_user(conn: sqlite3.Connection, admin, user_id: int) -> None:
    """The account goes; what it added stays in the shared collection."""
    if user_id == admin["id"]:
        raise ValueError("You can't remove your own account.")
    row = conn.execute("SELECT * FROM users WHERE id = ? AND collection_id = ?",
                       (user_id, admin["collection_id"])).fetchone()
    if row is None:
        raise ValueError("No such account.")
    if row["id"] == row["collection_id"]:
        # The collection's rows point at this account's id.
        raise ValueError("That's the account the collection belongs to, so it can't be removed.")
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.execute("DELETE FROM user_theme WHERE user_id = ?", (user_id,))
    conn.commit()


def set_admin(conn: sqlite3.Connection, admin, user_id: int, is_admin: bool) -> None:
    if user_id == admin["id"] and not is_admin:
        raise ValueError("You can't take away your own admin rights.")
    conn.execute("UPDATE users SET is_admin = ? WHERE id = ? AND collection_id = ?",
                 (int(is_admin), user_id, admin["collection_id"]))
    conn.commit()


SETUP_CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No 0/O or 1/I to mix up


def new_setup_code() -> str:
    return "".join(secrets.choice(SETUP_CODE_LETTERS) for _ in range(8))


class Lockout:
    """After `limit` wrong passwords for a username from one address within `window` seconds, that
    username can't log in from that address for `window` seconds. Other addresses aren't affected,
    so a stranger can't lock you out. Kept in memory, so a restart clears it.

    One address also gets `address_limit` wrong passwords in `address_window`
    seconds over all usernames, so trying many names from one place is limited too."""

    def __init__(self, limit: int = 5, window: float = 600, address_limit: int = 20, address_window: float = 900):
        self.limit, self.window = limit, window
        self.address_limit, self.address_window = address_limit, address_window
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._by_address: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, username: str, address: str) -> list[float]:
        now = time.monotonic()
        # Forget addresses that have gone quiet, so the list can't grow without end.
        if len(self._failures) > 1000:
            self._failures = {k: v for k, v in self._failures.items() if v and now - v[-1] < self.window}
        key = (username.strip().casefold(), address)
        times = [t for t in self._failures.get(key, []) if now - t < self.window]
        self._failures[key] = times
        return times

    def _address_recent(self, address: str) -> list[float]:
        now = time.monotonic()
        if len(self._by_address) > 1000:
            self._by_address = {k: v for k, v in self._by_address.items() if v and now - v[-1] < self.address_window}
        times = [t for t in self._by_address.get(address, []) if now - t < self.address_window]
        self._by_address[address] = times
        return times

    def locked_for(self, username: str, address: str = "") -> int:
        """Seconds until the username can try again from this address; 0 if it can now."""
        with self._lock:
            now, wait = time.monotonic(), 0
            times = self._recent(username, address)
            if len(times) >= self.limit:
                wait = max(int(self.window - (now - times[-1])) + 1, 1)
            many = self._address_recent(address)
            if len(many) >= self.address_limit:
                wait = max(wait, int(self.address_window - (now - many[-1])) + 1, 1)
            return wait

    def failed(self, username: str, address: str = "") -> None:
        with self._lock:
            now = time.monotonic()
            self._recent(username, address).append(now)
            self._address_recent(address).append(now)

    def succeeded(self, username: str, address: str = "") -> None:
        """The right password: that username's count is cleared. The address's count stays, so one known
        password can't be used to keep guessing others."""
        with self._lock:
            self._failures.pop((username.strip().casefold(), address), None)


def _cli(argv: list[str]) -> int:
    import getpass

    from . import db

    if len(argv) != 2 or argv[0] != "reset-password":
        print("Usage: python -m app.auth reset-password <username>")
        return 2
    conn = db.connect()
    db.init(conn)
    row = conn.execute("SELECT id FROM users WHERE username = ?", (argv[1],)).fetchone()
    if row is None:
        print(f"No account called {argv[1]}.")
        return 1
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Again: "):
        print("The passwords don't match.")
        return 1
    try:
        set_password(conn, row["id"], password)
    except ValueError as e:
        print(e)
        return 1
    print(f"Password changed for {argv[1]}. Other devices are signed out.")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli(sys.argv[1:]))
