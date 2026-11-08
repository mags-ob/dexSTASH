"""Draws docs/architecture-overview.svg and docs/architecture-detailed.svg. Run it after a change to the app's
shape (a new route module, worker, table or outside source), then check the numbers it prints against the code:

    python tools/make_diagrams.py
"""
import re
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

STYLE = """
svg{--bg:#ffffff;--fg:#1b2330;--muted:#5d6878;--line:#c7cfda;--accent:#7c4dcc;--off:#b5651d;
--py:#2f6fb3;--py-bg:#e3edf8;--html:#c4532a;--html-bg:#fbe8df;--js:#8a6d00;--js-bg:#fbf1c9;
--sql:#137a6b;--sql-bg:#dcf2ee;--cfg:#6a4fb0;--cfg-bg:#ece6f8;--csv:#4a7a22;--csv-bg:#e6f2da;
font-family:-apple-system,"Segoe UI",system-ui,Roboto,sans-serif}
@media (prefers-color-scheme: dark){svg{--bg:#0d1117;--fg:#e6eaf0;--muted:#9aa5b5;--line:#3a4453;--accent:#b99af5;--off:#f0a868;
--py:#8cbcf0;--py-bg:#1d3047;--html:#f29a77;--html-bg:#3d2318;--js:#e8cc5c;--js-bg:#3a3214;
--sql:#6fd6c4;--sql-bg:#143631;--cfg:#b9a3f2;--cfg-bg:#2b2443;--csv:#a5d679;--csv-bg:#22331a}}
.bg{fill:var(--bg)}
.box{fill:var(--bg);stroke:var(--line);stroke-width:1.5}
.box.hot{stroke:var(--accent);stroke-width:2}
.box.off{stroke:var(--off);stroke-width:1.5}
.ext{stroke-dasharray:4 3}
.ctr{fill:none;stroke:var(--muted);stroke-width:1.5;stroke-dasharray:6 5}
.t{fill:var(--fg);font-weight:700;font-size:16px}
.t2{fill:var(--fg);font-weight:700;font-size:13.5px}
.d{fill:var(--muted);font-size:12.5px}
.ds{fill:var(--muted);font-size:11.5px}
.m{fill:var(--fg);font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;font-size:12px;font-weight:600}
.lbl,.kt{font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;font-size:11px}
.lbl{fill:var(--muted)}
.offt{fill:var(--off);font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;font-size:11px}
.edge{stroke:var(--fg);stroke-width:1.5;fill:none}
.edge.soft{stroke:var(--muted);stroke-dasharray:4 3}
.ah{fill:var(--fg)}
.k-py{fill:var(--py-bg)}.k-py+text{fill:var(--py)}
.k-html{fill:var(--html-bg)}.k-html+text{fill:var(--html)}
.k-js{fill:var(--js-bg)}.k-js+text{fill:var(--js)}
.k-sql{fill:var(--sql-bg)}.k-sql+text{fill:var(--sql)}
.k-cfg{fill:var(--cfg-bg)}.k-cfg+text{fill:var(--cfg)}
.k-csv{fill:var(--csv-bg)}.k-csv+text{fill:var(--csv)}
"""
BADGE_WIDTH = 7.0  # px per monospace character at 11px, plus padding below


def facts() -> dict:
    """Counts read from the code, so the diagrams can't drift from it silently."""
    routes = {p.stem: len(re.findall(r"^@router\.", p.read_text(), re.M))
              for p in sorted((ROOT / "app/routes").glob("*.py")) if p.stem != "__init__"}
    tables, catalog = _tables()
    from_init = (ROOT / "app/__init__.py").read_text()
    return {"routes": routes, "route_total": sum(routes.values()), "tables": tables, "catalog": catalog,
            "modules": len([p for p in (ROOT / "app").glob("*.py") if p.stem != "__init__"]),
            "templates": len(list((ROOT / "app/templates").glob("*.html"))),
            "version": re.search(r'__version__ = "([^"]+)"', from_init).group(1)}


def _tables() -> tuple[list[str], list[str]]:
    """The tables a brand-new database gets, made the way the app makes it, and those in its catalog file."""
    import sys
    import tempfile
    sys.path.insert(0, str(ROOT))
    from app import db
    with tempfile.TemporaryDirectory() as folder:
        conn = db.connect(Path(folder) / "new.db")
        db.init(conn)
        names = [[r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' "
                                             "AND name NOT LIKE 'sqlite_%' ORDER BY name")] for schema in ("main", "catalog")]
        conn.close()
    return names[0], names[1]


class Svg:
    def __init__(self, w: int, h: int, title: str, label: str):
        self.w, self.h, self.out = w, h, []
        self.head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
                     f'aria-label="{escape(label)}"><title>{escape(title)}</title>\n<defs><style>{STYLE}</style>\n'
                     '<marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                     'orient="auto-start-reverse"><path class="ah" d="M0,0 L10,5 L0,10 z"/></marker></defs>\n'
                     f'<rect class="bg" x="0" y="0" width="{w}" height="{h}"/>\n')

    def add(self, s: str) -> None:
        self.out.append(s)

    def text(self, x, y, s, cls="d", anchor=None) -> None:
        a = f' text-anchor="{anchor}"' if anchor else ""
        self.add(f'<text class="{cls}" x="{x}" y="{y}"{a}>{escape(s)}</text>')

    def badge(self, x, y, s, kind) -> float:
        w = len(s) * BADGE_WIDTH + 14
        self.add(f'<rect class="k-{kind}" x="{x}" y="{y}" width="{w:.0f}" height="20" rx="4"/>'
                 f'<text class="kt" x="{x + 7}" y="{y + 14}">{escape(s)}</text>')
        return w

    def box(self, x, y, w, h, title=None, lines=(), cls="", badges=(), rx=8, title_cls="t", line_cls="d", gap=18) -> None:
        self.add(f'<rect class="box {cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"/>')
        ty = y + 26
        if title:
            self.text(x + 16, ty, title, title_cls)
        bx = x + w - 12
        for s, kind in reversed(badges):
            bw = len(s) * BADGE_WIDTH + 14
            bx -= bw
            self.badge(bx, y + 11, s, kind)
            bx -= 6
        for i, line in enumerate(lines):
            self.text(x + 16, ty + 22 + i * gap, line, line_cls)

    def container(self, x, y, w, h, title, note=None) -> None:
        self.add(f'<rect class="ctr" x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/>')
        self.text(x + 18, y + 28, title, "t")
        if note:
            self.text(x + w - 16, y + 28, note, "lbl", "end")

    def arrow(self, d, label=None, at=None, soft=False, anchor="middle", both=False) -> None:
        start = ' marker-start="url(#ar)"' if both else ""
        self.add(f'<path class="edge{" soft" if soft else ""}" d="{d}" marker-end="url(#ar)"{start}/>')
        if label:
            self.text(at[0], at[1], label, "lbl", anchor)

    def save(self, path: Path) -> None:
        path.write_text(self.head + "\n".join(self.out) + "\n</svg>\n", encoding="utf-8")


ALWAYS = [("TCGdex", "sets, cards, pictures, prices TCGCSV lacks"), ("TCGCSV", "TCGplayer's daily prices and pictures"),
          ("European Central Bank", "daily exchange rates"), ("PokéAPI", "names, Pokédex data, sprites"),
          ("CLIP model", "once, when Enhanced Search is on")]
KEYED = [("CardTrader API", "lowest listings"), ("PriceCharting API", "ungraded prices")]
SWITCHED = [("Limitless TCG", "Japanese card pictures"), ("Chinese card datasets", "mainland sets and pictures"),
            ("Bulbapedia", "Pokémon descriptions")]


def sources(s: Svg, x, y, w, line_gap=34) -> tuple[int, int, int]:
    """The three internet boxes; gives back the y of each box's middle and the bottom."""
    h1 = 44 + len(ALWAYS) * line_gap
    s.box(x, y, w, h1, "Always on", cls="ext")
    for i, (name, what) in enumerate(ALWAYS):
        s.text(x + 16, y + 52 + i * line_gap, name, "t2")
        s.text(x + 16, y + 67 + i * line_gap, what, "ds")
    yk = y + h1 + 22
    hk = 62 + len(KEYED) * line_gap
    s.box(x, yk, w, hk, "With your own API key", cls="ext", title_cls="t2")
    s.text(x + 16, yk + 44, "Data sources › Prices › API keys", "lbl")
    for i, (name, what) in enumerate(KEYED):
        s.text(x + 16, yk + 70 + i * line_gap, name, "t2")
        s.text(x + 16, yk + 85 + i * line_gap, what, "ds")
    y2 = yk + hk + 22
    h2 = 62 + len(SWITCHED) * line_gap
    s.box(x, y2, w, h2, "Switches, off on a new install", cls="ext off", title_cls="t2")
    s.text(x + 16, y2 + 44, "Data sources › Price and picture sites", "offt")
    for i, (name, what) in enumerate(SWITCHED):
        s.text(x + 16, y2 + 70 + i * line_gap, name, "t2")
        s.text(x + 16, y2 + 85 + i * line_gap, what, "ds")
    return y + h1 // 2, yk + hk // 2, y2 + h2 // 2, y2 + h2


def overview(f: dict) -> Svg:
    s = Svg(1200, 700, "dexSTASH architecture overview",
            "A phone connects over HTTPS to your reverse proxy, which passes requests to the dexstash container on port "
            "8000. There, FastAPI routes call feature modules and background workers. A second container, "
            "dexstash-scanner, reads scan photos at low priority. Both keep everything in the data folder: one SQLite "
            "database plus backups, photos, pictures and models. Every request to another site goes through "
            "app/sites.py, which sends one User-Agent and refuses sites that are switched off. TCGdex, TCGCSV, the "
            "European Central Bank and PokéAPI are always on; CardTrader's and PriceCharting's APIs need your own key; "
            "Limitless TCG, "
            "the Chinese card datasets and Bulbapedia are switches that start off on a new install. TCGplayer's and "
            "PriceCharting's pages are never read.")
    # Phone and proxy
    s.box(20, 190, 175, 230, "Phone browser", rx=18)
    s.badge(36, 226, "Jinja HTML", "html")
    s.badge(36, 250, "htmx", "js")
    s.badge(84, 250, "JavaScript", "js")
    for i, line in enumerate(["Pages and forms", "Camera photos, shrunk", "before they're sent",
                              "Style, scripts, font and", "icons ship with the app"]):
        s.text(36, 300 + i * 18, line)
    s.arrow("M195 300 H232", "HTTPS", (214, 291))
    s.box(235, 240, 140, 120, "Reverse proxy", ["Caddy, or any", "proxy with HTTPS"])
    s.badge(251, 320, "Caddyfile", "cfg")
    s.arrow("M375 300 H418", ":8000", (397, 291))

    # dexstash container
    s.container(400, 20, 470, 470, "Container “dexstash”", "1 CPU · 2 GB cap")
    s.box(420, 56, 430, 112, "Web app · FastAPI + Uvicorn", [
        f"app/routes/: {len(f['routes'])} page modules, {f['route_total']} routes",
        "app/web.py: templates, sign-in, shared helpers",
        "app/main.py: start-up, middleware, the routers"], cls="hot", badges=[("Python", "py")])
    s.arrow("M635 168 V188")
    s.box(420, 190, 430, 104, "Feature modules", [
        "sets and completion · cards · Find · My Stash", "binders · chases · spending · prices · themes",
        "Collectr import · backups · accounts · first run"], badges=[("Python", "py")])
    s.arrow("M635 294 V314", "slow work", (645, 309), anchor="start")
    s.box(420, 316, 430, 112, "Job runner · app/jobs.py", [
        "one job at a time; pages come first",
        "nightly prices and backup · picture check",
        "Collectr matcher · downloads · catalog fetch"], badges=[("Python", "py")])
    s.box(420, 440, 430, 38, None, cls="hot")
    s.text(436, 464, "app/sites.py: one User-Agent, switch check", "m")

    # scanner container
    s.container(400, 510, 470, 160, "Container “dexstash-scanner”", "low priority")
    s.box(420, 546, 430, 108, "Scanner · python -m app.scan_worker", [
        "Reads photos: name, set code, number (RapidOCR)",
        "Enhanced Search: matches by picture (CLIP, ONNX)",
        "Picture lookups and fingerprints"], badges=[("Python", "py")])
    s.arrow("M635 510 V496", "queues in the database", (645, 505), anchor="start", soft=True)

    # storage
    s.box(20, 450, 355, 220, "Your data/ folder", [
        f"dexstash.db: your data, {len(f['tables'])} tables",
        f"dexstash-catalog.db: downloads, {len(f['catalog'])} tables",
        "backups/: before upgrades, nightly, by hand",
        "scans/, card-photos/: your photos",
        "models/, index/: Enhanced Search",
        "card-pictures/, logs/, secret_key, api_keys.json",
        "Back up this folder and .env: that's",
        "everything the app knows."], badges=[("SQLite", "sql")])
    s.arrow("M400 470 H378")
    s.arrow("M400 600 H378")

    # outside
    s.text(905, 40, "The internet", "t")
    mid1, midk, mid2, bottom = sources(s, 905, 56, 280)
    s.arrow("M850 459 H880 V%d H903" % mid1)
    s.arrow("M880 459 V%d H903" % midk)
    s.arrow("M880 459 V%d H903" % mid2)
    s.add('<path class="edge" d="M850 600 H880 V459"/>')
    for i, line in enumerate(["Every outside request, from either container,", "goes through app/sites.py. A switched-off",
                              "site gets no request, and turning one off", "deletes nothing."]):
        s.text(905, bottom + 26 + i * 16, line, "ds")
    return s


def detailed(f: dict) -> Svg:
    r = f["routes"]
    s = Svg(1400, 1020, "dexSTASH detailed architecture",
            "Every part of dexSTASH: the phone, the reverse proxy, the dexstash container with its route modules, "
            "shared web layer, feature modules grouped by area and background workers; the dexstash-scanner container; "
            "the data folder with the SQLite tables grouped by area; app/sites.py; and the outside sources, always on or "
            "behind a switch.")
    s.box(20, 20, 200, 170, "Phone browser", ["Jinja pages, htmx forms", "Camera: live or one photo", "Add to Home Screen",
                                              "Card pictures you don't", "keep load from TCGdex"], rx=18)
    s.arrow("M120 190 V218", "HTTPS", (130, 209), anchor="start")
    s.box(20, 220, 200, 90, "Reverse proxy", ["HTTPS, request_body 25MB", "→ dexstash:8000"])

    s.container(250, 20, 760, 640, "Container “dexstash” · one Uvicorn worker", f"version {f['version']}")
    # web layer
    s.box(270, 56, 720, 196, "Web layer", [], cls="hot", badges=[("Python", "py"), ("Jinja", "html")])
    s.text(286, 102, "app/main.py", "m")
    s.text(286, 120, "start-up and shutdown, middleware", "ds")
    s.text(286, 136, "(Origin check, sessions, proxy", "ds")
    s.text(286, 152, "addresses), static files, routers", "ds")
    s.text(286, 182, "app/web.py", "m")
    s.text(286, 200, "templates and filters, sign-in", "ds")
    s.text(286, 216, "checks, the workers, shared", "ds")
    s.text(286, 232, "helpers for several pages", "ds")
    s.text(520, 84, f"app/routes/ · {f['route_total']} routes in {len(r)} modules", "t2")
    names = sorted(r, key=lambda k: -r[k])
    for i, name in enumerate(names):
        col, row = divmod(i, 6)
        s.text(520 + col * 158, 108 + row * 18, f"{name}.py", "m")
        s.text(520 + col * 158 + 142, 108 + row * 18, str(r[name]), "lbl", "end")
    s.text(520, 220, f"{f['templates']} templates in app/templates/ · app/static/ (CSS, JS, htmx, font, logo)", "ds")
    s.text(520, 238, "Each module has its own APIRouter; app/main.py includes them all.", "ds")

    s.arrow("M630 252 V270")
    groups = [
        ("Collection", ["inventory · catalog · numbers", "collection · binders · shared", "chase · wants · notes · tags",
                        "spending · value · home · smart"]),
        ("Find", ["search · describe · names", "spelling · looks_like · evolution", "handmade · pokeapi · display",
                  "region · pokemon_words · card_index"]),
        ("Prices and links", ["prices (one rule) · sync (nightly)", "tcgcsv · cardtrader · pricecharting",
                              "fx (currency) · keys (API keys)",
                              "extras (your links, never read)"]),
        ("Pictures and themes", ["card_pictures · pictures (CLIP)", "themes · icons", "scans · scanner (queue)", ""]),
        ("Data and import", ["tcgdex · chinese · bulbapedia", "importer · collectr", "", ""]),
        ("Platform", ["db (numbered steps) · auth", "backup · reset · settings", "activity (log) · recent",
                      "welcome (first-run wizard)"]),
    ]
    for i, (title, lines) in enumerate(groups):
        x = 270 + (i % 3) * 244
        y = 272 + (i // 3) * 124
        s.box(x, y, 232, 114, title, lines, title_cls="t2", line_cls="ds", gap=17)
    s.text(270, 532, f"{f['modules']} Python modules in app/, each taking a database connection", "ds")

    s.box(270, 546, 476, 100, "Job runner · app/jobs.py · one job at a time", [
        "PriceUpdater + Nightly: prices, new sets, backup",
        "Checker: saved pictures · Matcher: Collectr",
        "Downloads and card catalog fetch; pages come first"], badges=[("Python", "py")], title_cls="t2", line_cls="ds")
    s.box(758, 546, 232, 100, "app/sites.py", ["One User-Agent:", "dexSTASH/<version> (+repo)", "refuses switched-off sites"],
          cls="hot", title_cls="t2", line_cls="ds")

    # scanner
    s.container(250, 690, 760, 170, "Container “dexstash-scanner” · low priority", "python -m app.scan_worker")
    s.box(270, 726, 232, 118, "scans.Worker", ["RapidOCR reads name,", "set code and number;", "matches to a card"],
          title_cls="t2", line_cls="ds", badges=[("ONNX", "py")])
    s.box(514, 726, 232, 118, "scans.PictureWorker", ["Enhanced Search: CLIP", "fingerprints, 8 closest", "in every language"],
          title_cls="t2", line_cls="ds", badges=[("ONNX", "py")])
    s.box(758, 726, 232, 118, "LookupWorker + jobs", ["Picture lookups,", "fingerprint indexes,", "followed cards' art"],
          title_cls="t2", line_cls="ds")
    s.arrow("M630 690 V660", "queued in the database", (640, 680), anchor="start", soft=True)
    s.arrow("M800 726 V648")

    # data
    t = f["tables"]
    s.box(20, 340, 210, 520, "data/", [], badges=[("SQLite", "sql")])
    s.text(36, 386, "dexstash.db", "m")
    s.text(36, 404, f"{len(t)} tables, WAL mode", "ds")
    areas = [("cards", "sets · cards · prices"), ("yours", "inventory · notes"), ("", "binders · wants · pulls"),
             ("", "purchases · tags"), ("scans", "scan_batches · scans"), ("prices", "price_snapshots"),
             ("system", "users · app_meta"), ("", "jobs · datasets"), ("", f"+ catalog: {len(f['catalog'])} tables")]
    for i, (_, line) in enumerate(areas):
        s.text(36, 428 + i * 17, line, "ds")
    folders = ["backups/", "scans/ · card-photos/", "card-pictures/", "models/ · index/", "sprites/ · pokeapi/",
               "bulbapedia/ · logs/", "secret_key · api_keys.json"]
    for i, line in enumerate(folders):
        s.text(36, 600 + i * 18, line, "m")
    s.text(36, 740, "Shared by both", "ds")
    s.text(36, 756, "containers.", "ds")
    s.text(36, 780, "Back it up with .env:", "ds")
    s.text(36, 796, "it is everything the", "ds")
    s.text(36, 812, "app knows.", "ds")
    s.arrow("M250 500 H232")
    s.arrow("M250 790 H232")

    # outside
    s.text(1040, 40, "The internet", "t")
    mid1, midk, mid2, bottom = sources(s, 1040, 56, 340)
    s.arrow("M990 596 H1015 V%d H1038" % mid1)
    s.arrow("M1015 596 V%d H1038" % midk)
    s.arrow("M1015 596 V%d H1038" % mid2)
    s.text(1040, bottom + 26, "Hosts each source uses are listed in the", "ds")
    s.text(1040, bottom + 42, "README under “Where your data comes from”.", "ds")
    s.text(20, 900, "Solid boxes run on your server; dashed boxes are on the internet. Orange boxes are switches.", "ds")
    s.text(20, 918, "Colours mark each part's language: blue Python, orange HTML and Jinja, yellow JavaScript and htmx, "
                    "teal SQL, purple configuration.", "ds")
    s.h = 940
    s.head = s.head.replace('viewBox="0 0 1400 1020" width="1400" height="1020"', 'viewBox="0 0 1400 940" width="1400" height="940"')
    s.head = s.head.replace('width="1400" height="1020"/>', 'width="1400" height="940"/>')
    return s


if __name__ == "__main__":
    f = facts()
    overview(f).save(DOCS / "architecture-overview.svg")
    detailed(f).save(DOCS / "architecture-detailed.svg")
    print(f"{f['route_total']} routes in {len(f['routes'])} modules, {len(f['tables'])} tables, {f['modules']} modules, "
          f"{f['templates']} templates, version {f['version']}")
