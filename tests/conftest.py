import os
import tempfile

os.environ["DEXSTASH_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DEXSTASH_HTTPS_ONLY"] = "0"
os.environ.setdefault("DEXSTASH_SECRET_KEY", "test-key-" + "x" * 40)
os.environ.setdefault("DEXSTASH_SCANNER", "threads")  # round 15c: photos read in the app's own threads, as before
os.environ.setdefault("DEXSTASH_PICTURE_CHECK", "0")  # tests run the picture check by hand
os.environ.pop("DEXSTASH_CARDTRADER_TOKEN", None)  # round 15b: CardTrader is tested with saved sample replies
os.environ.setdefault("DEXSTASH_TCGDEX_CACHE", "0")  # saved TCGdex replies are tested on their own
os.environ.setdefault("DEXSTASH_TCGCSV", "0")  # round 11: TCGCSV is tested with saved sample files
os.environ.setdefault("DEXSTASH_EURO_RATE", "0")  # and the ECB rate with a saved reply
os.environ.setdefault("DEXSTASH_CARD_INDEX", "0")  # round 15d: the local card catalog is tested on its own

from app import sites  # noqa: E402

# Round 14: the tests' database is a new install, so every price and picture site would start switched off.
# Tests use them (with fake fetches); tests of the switches themselves put this back to None.
sites._override = {s: True for s in sites.SITES}

from app import sync  # noqa: E402

# Round 20: the short price run after an add runs on a thread of its own; in tests it runs at once on the app's
# connection (0.20.1: pages no longer fetch TCGdex's price, so the short run is what prices an added card).
sync.PriceUpdater.inline = staticmethod(lambda: __import__("app.main", fromlist=["conn"]).conn)

from app import welcome  # noqa: E402

# Round 22: importing the app no longer sets the database up; the tests start it the way the start-up hook does
# (without its background part, which tests run by hand).
from app import main as _main, startup as _startup  # noqa: E402
_startup.settings_phase()
_startup.before_first_page(_main.conn, _main.web.BACKUPS)

# Round 16: tests sign in to an install that has been set up; the wizard's own tests put this back to None.
welcome._override = True


def with_assets(html: str) -> str:
    """Round 13: a page with the CSS and scripts it links to put back inline, for tests that check them."""
    import re
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "app" / "static"

    def body(m):
        return m.group(0) + "\n" + (static / m.group(1)).read_text()
    return re.sub(r'<(?:script src|link rel="stylesheet" href)="/static/((?:css|js)/[^"?]+)\?v=[^"]*"[^>]*>', body, html)


def template_with_assets(name: str) -> str:
    """A template's source with the CSS and scripts it links to (by asset("...")) added."""
    import re
    from pathlib import Path
    app = Path(__file__).resolve().parent.parent / "app"
    text = (app / "templates" / name).read_text()
    return text + "".join("\n" + (app / "static" / p).read_text() for p in re.findall(r'asset\("([^"]+\.(?:css|js))"\)', text))
