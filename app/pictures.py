"""Enhanced Search: when a photo's text can't settle which card it is, compare
the photo with fingerprints of the card pictures of every set in English,
Japanese and mainland Chinese.

1. The model is the CLIP ViT-B/32 picture encoder as an ONNX file (about
   350 MB), run with onnxruntime like the photo reader. It downloads into
   data/models/ when Enhanced Search is turned on (Data sources), loads only
   while photos are being matched or pictures fingerprinted, never at the
   same time as the photo reader, and unloads after IDLE_SECONDS.
2. Pictures are downloaded one at a time, fingerprinted and let go: only the
   fingerprints are kept. Photos you added cards with stay where they are.
3. Each picture becomes 512 numbers. One index per language sits in
   data/index/<lang>.npy, with <lang>.json saying which card each row is
   (with its name, number and set, so cards from sets that aren't stored here
   can be shown). Searching is one numpy multiplication.
4. A photo is cropped to the card twice (against the background, and from
   where the reader found text), and the crops' fingerprints are averaged.
"""

import gc
import hashlib
import io
import json
import logging
import threading
import time
from pathlib import Path

from . import catalog, chinese, db, handmade, scanner, settings, sites, tcgdex

log = logging.getLogger("dexstash.pictures")

MODEL_URL = ("https://clip-as-service.s3.us-east-2.amazonaws.com/models-436c69702d61732d53657276696365/"
             "onnx/ViT-B-32/visual.onnx")
MODEL_SHA256 = "06395063c0a5c28b1a8d4bd585261501a878c8f52d1216db6c4cbb651f7c13f1"
MODEL_BYTES = 351_520_836
DATA = db.DB_PATH.parent
MODELS = DATA / "models"
IMAGES = DATA / "card-images"  # pictures earlier versions kept; deleted on start
INDEX = DATA / "index"
IDLE_SECONDS = 300
MATCHES = 8
DIM = 512
SIDE = 224
_MEAN = (0.48145466, 0.4578275, 0.40821073)
_STD = (0.26862954, 0.26130258, 0.27577711)
CARD_RATIO = 63 / 88


def available() -> bool:
    """numpy, Pillow and onnxruntime: all three come with the photo reader."""
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def model_path() -> Path:
    return MODELS / "clip-vit-b32-visual.onnx"


def has_model() -> bool:
    return model_path().exists()


def is_on(conn) -> bool:
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'pictures_on'").fetchone()
    return bool(row and row["value"] == "1")


def set_on(conn, on: bool) -> None:
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('pictures_on', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", ("1" if on else "0",))
    conn.commit()


def download_model(job=None, url: str | None = None) -> Path:
    """Fetch the model once, checking it's the file we expect."""
    path = model_path()
    if path.exists():
        return path
    url = url or settings.get("PICTURE_MODEL_URL") or MODEL_URL
    MODELS.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    digest = hashlib.sha256()
    with sites.client(timeout=60, follow_redirects=True) as client, client.stream("GET", url) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or MODEL_BYTES)
        done = 0
        with part.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if job is not None:
                    job.progress(done >> 20, total >> 20, "downloading the picture model (MB)")
    if url == MODEL_URL and digest.hexdigest() != MODEL_SHA256:
        part.unlink(missing_ok=True)
        raise ValueError("The picture model didn't download correctly. Try again.")
    part.replace(path)
    return path


# --- turning pictures into numbers ------------------------------------------

def _span(profile, frac: float, gap: int):
    """The longest run where profile > frac, letting gaps shorter than `gap` through."""
    import numpy as np
    idx = np.flatnonzero(profile > frac)
    if not len(idx):
        return None
    best, start, prev = None, idx[0], idx[0]
    for i in list(idx[1:]) + [None]:
        if i is None or i - prev > gap:
            if best is None or prev - start > best[1] - best[0]:
                best = (int(start), int(prev) + 1)
            if i is not None:
                start = i
        if i is not None:
            prev = i
    return best


def background_crop(image):
    """The card cut out from a plain background (a table, a mat), or None
    when it doesn't stand out clearly."""
    import numpy as np
    small = image.convert("RGB")
    small.thumbnail((320, 320))
    a = np.asarray(small, np.float32)
    h, w, _ = a.shape
    b = max(3, min(h, w) // 30)
    border = np.concatenate([a[:b].reshape(-1, 3), a[-b:].reshape(-1, 3), a[:, :b].reshape(-1, 3), a[:, -b:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    spread = np.percentile(np.abs(border - bg).sum(1), 95)
    fg = np.abs(a - bg).sum(2) > max(45, spread * 1.3)
    rows = _span(fg.mean(1), 0.25, max(3, h // 15))
    cols = _span(fg.mean(0), 0.25, max(3, w // 15))
    if not rows or not cols:
        return None
    ch, cw = rows[1] - rows[0], cols[1] - cols[0]
    if ch < h * 0.3 or cw < w * 0.3 or not (0.55 <= cw / ch <= 0.9 or 1.1 <= cw / ch <= 1.8):
        return None
    sx, sy = image.width / w, image.height / h
    return image.crop((int(cols[0] * sx), int(rows[0] * sy), int(cols[1] * sx), int(rows[1] * sy)))


def text_crop(image, box):
    """The card where the reader found its text; box is scanner.card_box's (x0, y0, x1, y1) as fractions."""
    if not box:
        return None
    x0, y0, x1, y1 = box
    rect = (int(x0 * image.width), int(y0 * image.height), int(x1 * image.width), int(y1 * image.height))
    if rect[2] - rect[0] < 32 or rect[3] - rect[1] < 32:
        return None
    return image.crop(rect)


def crops(image, box=None) -> list:
    """What of a photo to fingerprint: the card found both ways, else the whole photo."""
    found = [c for c in (background_crop(image), text_crop(image, box)) if c is not None]
    return found or [image]


def _pixels(image):
    """Squared up on white (cards are tall, the model wants a square) and scaled like CLIP was trained."""
    import numpy as np
    from PIL import Image
    image = image.convert("RGB")
    side = max(image.size)
    square = Image.new("RGB", (side, side), (255, 255, 255))
    square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    a = np.asarray(square.resize((SIDE, SIDE), Image.BICUBIC), np.float32) / 255
    a = (a - np.array(_MEAN, np.float32)) / np.array(_STD, np.float32)
    return a.transpose(2, 0, 1)


class Encoder:
    """The picture model, loaded on first use and let go after IDLE_SECONDS."""

    def __init__(self, path: Path | None = None, idle: float = IDLE_SECONDS):
        self.path, self.idle = path, idle
        self._session = None
        self._lock = threading.Lock()
        self._used = 0.0
        self._reaper: threading.Thread | None = None

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def embed(self, images: list):
        """One fingerprint (length 1) per picture, as a numpy array."""
        import numpy as np
        out = []
        for i in range(0, len(images), 8):
            batch = np.stack([_pixels(im) for im in images[i:i + 8]])
            with scanner.HEAVY, self._lock:  # never alongside the photo reader
                if self._session is None:
                    self._session = self._load()
                    self._start_reaper()
                out.append(self._session.run(None, {self._session.get_inputs()[0].name: batch})[0])
                self._used = time.monotonic()
        vectors = np.concatenate(out).astype(np.float32)
        return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)

    def _load(self):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1  # One thread, no spinning (the scanner has one CPU)
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        return ort.InferenceSession(str(self.path or model_path()), options, providers=["CPUExecutionProvider"])

    def unload_if_idle(self, now: float | None = None) -> bool:
        with self._lock:
            if self._session is not None and (now or time.monotonic()) - self._used >= self.idle:
                self._session = None
                gc.collect()
                return True
        return False

    def _start_reaper(self) -> None:
        if self._reaper is not None and self._reaper.is_alive():
            return

        def watch():
            while self._session is not None:
                time.sleep(min(30, self.idle))
                self.unload_if_idle()

        self._reaper = threading.Thread(target=watch, daemon=True)
        self._reaper.start()


def photo_vector(encoder, image, box=None):
    """One fingerprint for a photo: the average of its crops'."""
    import numpy as np
    vectors = encoder.embed(crops(image, box))
    v = vectors.mean(axis=0)
    return v / max(float(np.linalg.norm(v)), 1e-9)


# --- the index ----------------------------------------------------------------

class Index:
    """Fingerprints of one language's card pictures. rows[i] says what
    vectors[i] is: {"card": our card id, "set": our set id, "key": the picture}."""

    def __init__(self, lang: str, folder: Path | None = None):
        import numpy as np
        self.lang, self.folder = lang, folder or INDEX
        self.lock = threading.RLock()  # a search never sees rows and vectors out of step
        self.rows: list[dict] = []
        self.vectors = np.zeros((0, DIM), np.float32)
        npy, meta = self._files()
        if npy.exists() and meta.exists():
            try:
                self.vectors = np.load(npy).astype(np.float32)
                self.rows = json.loads(meta.read_text())
            except Exception as e:
                log.warning("picture index %s unreadable, starting again: %s", lang, e)
            if len(self.rows) != len(self.vectors):
                self.rows, self.vectors = [], np.zeros((0, DIM), np.float32)

    def _files(self) -> tuple[Path, Path]:
        return self.folder / f"{self.lang}.npy", self.folder / f"{self.lang}.json"

    def keys(self) -> set[str]:
        return {r["key"] for r in self.rows}

    def sets(self) -> set[str]:
        return {r["set"] for r in self.rows}

    def put(self, rows: list[dict], vectors) -> None:
        import numpy as np
        if not rows:
            return
        new = {r["key"] for r in rows}
        with self.lock:
            keep = [i for i, r in enumerate(self.rows) if r["key"] not in new]
            self.rows = [self.rows[i] for i in keep] + list(rows)
            self.vectors = np.concatenate([self.vectors[keep], np.asarray(vectors, np.float32).reshape(-1, DIM)])

    def keep_only(self, keep) -> int:
        import numpy as np
        with self.lock:
            idx = [i for i, r in enumerate(self.rows) if keep(r)]
            dropped = len(self.rows) - len(idx)
            self.rows = [self.rows[i] for i in idx]
            self.vectors = self.vectors[idx] if idx else np.zeros((0, DIM), np.float32)
        return dropped

    def drop(self) -> None:
        """Forget this index, files and all."""
        with self.lock:
            self.rows, self.vectors = [], self.vectors[:0]
        for f in self._files():
            f.unlink(missing_ok=True)

    def save(self) -> None:
        import numpy as np
        self.folder.mkdir(parents=True, exist_ok=True)
        npy, meta = self._files()
        buf = io.BytesIO()
        with self.lock:
            np.save(buf, self.vectors.astype(np.float16))
            rows = list(self.rows)
        tmp = npy.with_suffix(".npy.tmp")
        tmp.write_bytes(buf.getvalue())
        tmp.replace(npy)
        tmp = meta.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows))
        tmp.replace(meta)

    def search(self, vector, k: int = MATCHES) -> list[tuple[dict, float]]:
        import numpy as np
        with self.lock:
            rows, vectors = self.rows, self.vectors
        if not rows:
            return []
        scores = vectors @ np.asarray(vector, np.float32)
        top = np.argsort(-scores)[:k * 3]
        return [(rows[i], float(scores[i])) for i in top]


_indexes: dict[str, Index] = {}
_index_lock = threading.Lock()


def index_for(lang: str) -> Index:
    with _index_lock:
        if lang not in _indexes or _indexes[lang].folder != INDEX:
            _indexes[lang] = Index(lang)
        return _indexes[lang]


def languages() -> list[str]:
    return sorted(p.stem for p in INDEX.glob("*.npy")) if INDEX.exists() else []


def forget_loaded() -> None:
    with _index_lock:
        _indexes.clear()


def stats() -> dict:
    """For the Data sources page."""
    per_lang = {lang: len(index_for(lang).rows) for lang in languages()}
    return {"per_lang": per_lang, "pictures": sum(per_lang.values()),
            "model_bytes": model_path().stat().st_size if model_path().exists() else 0}


def save_stats(conn) -> None:
    """The scanner writes the counts for the web app's Data sources page, so the app never loads
    the indexes itself."""
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('picture_stats', ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (json.dumps(stats()),))
    conn.commit()


def stats_saved(conn) -> dict:
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'picture_stats'").fetchone()
    saved = json.loads(row["value"]) if row else {"per_lang": {}, "pictures": 0}
    saved["model_bytes"] = model_path().stat().st_size if model_path().exists() else 0
    return saved


# --- which pictures, and where they come from -------------------------------------

LANGS = ("en", "ja", "zh-cn")  # Enhanced Search covers every set in these


def drop_kept_images() -> int:
    """Delete the TCGdex pictures earlier versions kept. Only fingerprints are kept now."""
    import shutil
    if not IMAGES.exists():
        return 0
    count = sum(1 for _ in IMAGES.rglob("*.webp"))
    shutil.rmtree(IMAGES, ignore_errors=True)
    return count


def _safe(number: str) -> str:
    return "".join(ch for ch in number if ch.isalnum() or ch in "-_") or "x"


def all_sets(conn, api_for) -> tuple[list[dict], list[str]]:
    """Every set to fingerprint: TCGdex's English and Japanese sets and the
    mainland Chinese sets stored here. Also the languages TCGdex couldn't list."""
    out, failed = [], []
    for lang in ("en", "ja"):
        try:
            listed = api_for(lang).list_sets()
        except Exception as e:
            log.info("no %s set list: %s", lang, e)
            failed.append(lang)
            continue
        for s in reversed(listed):  # newest first
            out.append({"set_id": catalog.local_id(lang, s["id"]), "tcgdex": s["id"], "language": lang,
                        "name": s["name"], "count": (s.get("cardCount") or {}).get("total")})
    for r in conn.execute("SELECT set_id, name FROM sets WHERE language = ? ORDER BY release_date DESC, set_id",
                          (chinese.LANG,)):
        out.append({"set_id": r["set_id"], "tcgdex": None, "language": chinese.LANG, "name": r["name"], "count": None})
    return out, failed


def set_pictures(conn, s: dict, api_for=None) -> list[dict]:
    """{card, set, key, url, name, number, set_name} for each card of the set
    with a picture: from TCGdex for its sets, from the cards stored here for
    the mainland Chinese ones."""
    out = []
    if s.get("tcgdex"):
        data = api_for(s["language"]).get_set(s["tcgdex"])
        set_name = data.get("name") or s["name"]
        for c in data.get("cards", []):
            if not c.get("image"):
                continue
            out.append({"card": catalog.local_id(s["language"], c["id"]), "set": s["set_id"],
                        "key": f"tcgdex:{s['language']}/{s['tcgdex']}/{_safe(c['localId'])}",
                        "url": tcgdex.image_url(c["image"]), "name": c["name"], "number": c["localId"],
                        "set_name": set_name})
        return out
    for c in conn.execute("SELECT c.id, c.number, c.name, c.image_url, s.name AS set_name FROM cards c "
                          "JOIN sets s ON s.set_id = c.set_id WHERE c.set_id = ? ORDER BY c.id", (s["set_id"],)):
        url = c["image_url"] or ""
        if url.startswith("/chs-img/"):
            _, _, cid, n = url.split("/")
            key = f"chs:{cid}/{n}"
        elif url.startswith("/card-photos/"):
            key = f"hand:{url.rsplit('/', 1)[1]}"
        else:
            continue
        out.append({"card": c["id"], "set": s["set_id"], "key": key, "url": url, "name": c["name"],
                    "number": c["number"], "set_name": c["set_name"]})
    return out


def picture_image(pic: dict, fetch=None):
    """The picture, opened, without keeping it (photos you added are already here)."""
    from PIL import Image
    kind, _, rest = pic["key"].partition(":")
    if kind == "hand":
        path = handmade.photo_path(rest)
        if path is None:
            raise FileNotFoundError(rest)
        with Image.open(path) as im:
            return im.convert("RGB")
    if kind == "chs":
        cid, n = rest.split("/")
        found = chinese.picture(cid, n, keep=False)
        source = found if isinstance(found, Path) else io.BytesIO(found)
    else:
        source = io.BytesIO((fetch or _get)(pic["url"]))
    with Image.open(source) as im:
        return im.convert("RGB")


def _get(url: str) -> bytes:
    r = sites.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.content


# --- building -------------------------------------------------------------------

_again = threading.Event()


def ask_again() -> None:
    """A job is already indexing: have it look once more when it's done."""
    _again.set()


def _row(pic: dict) -> dict:
    return {k: pic[k] for k in ("card", "set", "key", "name", "number", "set_name", "url")}


def index_sets(conn, encoder, api_for, job=None, fetch=None) -> str:
    """Fingerprint every set's pictures that the index doesn't have yet. Each
    picture is downloaded, fingerprinted and let go. Sets the index already
    has all of are skipped without asking TCGdex."""
    done = failed = 0
    unreachable: list[str] = []
    while True:
        _again.clear()
        scope, unreachable = all_sets(conn, api_for)
        counts: dict[str, int] = {}
        for lang in LANGS:
            for r in index_for(lang).rows:
                counts[r["set"]] = counts.get(r["set"], 0) + 1
        todo = [s for s in scope if not (s["count"] and counts.get(s["set_id"], 0) >= s["count"])]
        for n, s in enumerate(todo):
            index = index_for(s["language"])
            label = s["name"] if s["language"] == "en" else (s["tcgdex"] or catalog.tcgdex_set_id(s["set_id"])).upper()
            if job is not None:
                job.progress(n, len(todo), f"{label} ({s['language'].upper()})")
            try:
                pics = set_pictures(conn, s, api_for)
            except Exception as e:
                log.info("couldn't list %s: %s", s["set_id"], e)
                failed += 1
                continue
            have = index.keys()
            new = [p for p in pics if p["key"] not in have]
            for i in range(0, len(new), 8):
                rows, images = [], []
                for pic in new[i:i + 8]:
                    try:
                        images.append(picture_image(pic, fetch))
                        rows.append(_row(pic))
                    except Exception as e:
                        failed += 1
                        log.info("no picture for %s: %s", pic["card"], e)
                if images:
                    index.put(rows, encoder.embed(images))
                    done += len(rows)
            # Pictures of cards that left the set (rare) go too; photos you added stay.
            wanted = {p["key"] for p in pics}
            index.keep_only(lambda r: r["set"] != s["set_id"] or r["key"] in wanted or r["key"].startswith("hand:"))
            index.save()
        if job is not None:
            job.progress(len(todo), len(todo))
        if not _again.is_set():
            break
    names = {"en": "English", "ja": "Japanese"}
    note = (f" Couldn't reach TCGdex for {' or '.join(names[l] for l in unreachable)}." if unreachable else "")
    if not done and not failed:
        return "Enhanced Search is up to date." + note
    text = f"Fingerprinted {done} picture{'' if done == 1 else 's'}."
    return text + (f" {failed} couldn't be fetched; Check for updates tries again." if failed else "") + note


def add_photo(conn, encoder, card_id: str, photo_name: str) -> None:
    """A photo a card was added by hand with, so the next copy is recognized."""
    from PIL import Image
    card = conn.execute("SELECT c.set_id, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?",
                        (card_id,)).fetchone()
    path = handmade.photo_path(photo_name)
    if card is None or path is None:
        return
    with Image.open(path) as im:
        vector = photo_vector(encoder, im.convert("RGB"))
    index = index_for(card["language"])
    index.put([{"card": card_id, "set": card["set_id"], "key": f"hand:{photo_name}"}], vector.reshape(1, DIM))
    index.save()


# --- learning from your scans -----------------------------------------------

LEARNED_PER_CARD = 5


def learn(conn, scan: dict, card_id: str) -> None:
    """A scan that went through Enhanced Search was added as card_id: keep its fingerprint (not the photo)
    under that card, and score where the card was in the list. The caller commits."""
    import numpy as np
    matches = json.loads(scan["picture_json"]) if scan.get("picture_json") else None
    if matches is None:
        return
    ours = catalog.tcgdex_set_id(card_id)
    rank = next((i + 1 for i, m in enumerate(matches) if m.get("id") == ours), 0)
    conn.execute("INSERT OR REPLACE INTO picture_scores (scan_id, rank, at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
                 (scan["id"], rank))
    vector = scan.get("picture_vector")
    card = conn.execute("SELECT c.set_id, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?",
                        (card_id,)).fetchone()
    if not vector or card is None or len(vector) != DIM * 4:
        return
    conn.execute("INSERT OR REPLACE INTO picture_learned (key, card_id, set_id, lang, vector, at) "
                 "VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
                 (f"scan:{scan['id']}", card_id, card["set_id"], card["language"],
                  np.frombuffer(vector, np.float32).astype(np.float32).tobytes()))
    conn.execute("""DELETE FROM picture_learned WHERE card_id = ? AND key NOT IN (
                        SELECT key FROM picture_learned WHERE card_id = ? ORDER BY at DESC, rowid DESC LIMIT ?)""",
                 (card_id, card_id, LEARNED_PER_CARD))


def unlearn(conn, scan_id: int) -> None:
    """The scan's match changed or it was taken out: forget what it taught. The caller commits."""
    conn.execute("DELETE FROM picture_learned WHERE key = ?", (f"scan:{scan_id}",))
    conn.execute("DELETE FROM picture_scores WHERE scan_id = ?", (scan_id,))


def score(conn) -> dict:
    row = conn.execute("SELECT COUNT(*), SUM(rank = 1), SUM(rank BETWEEN 1 AND 5) FROM picture_scores").fetchone()
    return {"scans": row[0] or 0, "first": row[1] or 0, "top5": row[2] or 0,
            "learned": conn.execute("SELECT COUNT(*) FROM picture_learned").fetchone()[0]}


def sync_learned(conn) -> int:
    """Bring each language's index in step with picture_learned: new fingerprints in, forgotten ones out.
    Returns how many rows changed."""
    import numpy as np
    rows = [dict(r) for r in conn.execute("SELECT key, card_id, set_id, lang, vector FROM picture_learned")]
    by_lang: dict[str, list[dict]] = {}
    for r in rows:
        by_lang.setdefault(r["lang"], []).append(r)
    changed = 0
    for lang in set(by_lang) | set(languages()):
        index = index_for(lang)
        want = {r["key"]: r for r in by_lang.get(lang, [])}
        new = [r for k, r in want.items() if k not in index.keys()]
        dropped = index.keep_only(lambda row: not row["key"].startswith("scan:") or row["key"] in want)
        if new:
            index.put([{"card": r["card_id"], "set": r["set_id"], "key": r["key"]} for r in new],
                      np.stack([np.frombuffer(r["vector"], np.float32) for r in new]))
        if new or dropped:
            index.save()
            changed += len(new) + dropped
    return changed


# --- searching -------------------------------------------------------------------

# A scan batch's language -> the picture indexes it searches, in order. Taiwan and Hong Kong cards
# are the same TCGdex cards as the Japanese ones (same set id and number), so a Chinese batch shows Japanese
# matches as their Taiwan version, after the mainland ones.
BATCH_INDEXES = {"en": ("en",), "ja": ("ja",), "zh-cn": ("zh-cn", "ja"), "zh": ("zh-cn", "ja"),
                 "zh-tw": ("ja",), "ko": ()}  # Per photo; Taiwan cards share Japanese art, no Korean index
SHOWN_AS = {("zh-cn", "ja"): "zh-tw", ("zh", "ja"): "zh-tw", ("zh-tw", "ja"): "zh-tw"}


def search(conn, encoder, image, box=None, k: int = MATCHES, batch_lang: str | None = None,
           vector_out: list | None = None) -> list[dict]:
    """The cards whose pictures look most like the photo, best first: {id (TCGdex id), set_id, lang,
    number, name, image, set_name, score (0-100)}. batch_lang keeps it to that batch's language
    (Chinese: mainland first, then Taiwan); None searches every indexed language."""
    have = languages()
    wanted = BATCH_INDEXES.get(batch_lang, (batch_lang,)) if batch_lang else tuple(have)
    langs = [lang for lang in wanted if lang in have]
    if not langs:
        return []
    vector = photo_vector(encoder, image, box)
    if vector_out is not None:
        vector_out.append(vector)
    found: dict[str, tuple[dict, float, int]] = {}
    for rank, lang in enumerate(langs):
        for row, score in index_for(lang).search(vector, k):
            if row["card"] not in found or (found[row["card"]][2], -found[row["card"]][1]) > (rank, -score):
                found[row["card"]] = (row, score, rank)
    out = []
    for row, score, rank in sorted(found.values(), key=lambda t: (t[2], -t[1]) if batch_lang else -t[1]):
        shown_as = SHOWN_AS.get((batch_lang, langs[rank])) if batch_lang else None
        card = conn.execute("SELECT c.id, c.set_id, c.number, c.name, c.image_url, s.name AS set_name, s.language "
                            "FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?", (row["card"],)).fetchone()
        if card is not None:
            card = dict(card)
        elif row.get("name"):  # a set that isn't stored here: the index knows enough to show it
            card = {"id": row["card"], "set_id": row["set"], "number": row["number"], "name": row["name"],
                    "image_url": row.get("url"), "set_name": row.get("set_name"),
                    "language": row["set"].split(":", 1)[0] if ":" in row["set"] else "en"}
        else:
            continue
        out.append({"id": catalog.tcgdex_set_id(card["id"]), "set_id": catalog.tcgdex_set_id(card["set_id"]),
                    "lang": shown_as or card["language"], "number": card["number"], "name": card["name"],
                    "image": card["image_url"] or row.get("url"),
                    "set_name": card["set_name"], "score": round(max(0.0, score) * 100)})
        if len(out) >= k:
            break
    return out
