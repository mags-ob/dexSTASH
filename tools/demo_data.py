"""Fills a new, empty data folder with a demo collection for screenshots and trying the app out: an admin
called misty (password pikachu123), one made-up set with drawn pictures (no real card art), some cards
owned, a purchase and prices. Nothing is downloaded.

    DEXSTASH_DB=/tmp/demo/data/dexstash.db DEXSTASH_HTTPS_ONLY=0 python tools/demo_data.py
    DEXSTASH_DB=/tmp/demo/data/dexstash.db DEXSTASH_HTTPS_ONLY=0 uvicorn app.main:app --port 8000
"""
import hashlib
import os
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if not os.environ.get("DEXSTASH_DB"):
    sys.exit("Set DEXSTASH_DB to a new, empty folder's dexstash.db first.")

from app import auth, catalog, card_pictures, db, inventory, prices, sites, spending, web  # noqa: E402

SET_ID = "demo"
# (name, type colour, rarity, market price)
CARDS = [
    ("Bulbasaur", "#78c850", "Common", 0.25), ("Ivysaur", "#78c850", "Uncommon", 0.40),
    ("Venusaur ex", "#78c850", "Double rare", 6.80), ("Charmander", "#f08030", "Common", 0.60),
    ("Charmeleon", "#f08030", "Uncommon", 0.75), ("Charizard ex", "#f08030", "Double rare", 24.50),
    ("Squirtle", "#6890f0", "Common", 0.45), ("Wartortle", "#6890f0", "Uncommon", 0.35),
    ("Blastoise ex", "#6890f0", "Double rare", 9.10), ("Pikachu", "#f8d030", "Common", 1.20),
    ("Raichu", "#f8d030", "Rare", 1.05), ("Psyduck", "#6890f0", "Common", 0.20),
    ("Staryu", "#6890f0", "Common", 0.15), ("Starmie", "#6890f0", "Rare", 0.90),
    ("Togepi", "#ee99ac", "Common", 0.30), ("Horsea", "#6890f0", "Common", 0.15),
    ("Gyarados ex", "#6890f0", "Double rare", 7.40), ("Lapras", "#6890f0", "Rare", 1.60),
    ("Eevee", "#a8a878", "Common", 0.80), ("Vaporeon", "#6890f0", "Rare", 2.30),
    ("Gengar ex", "#705898", "Double rare", 11.20), ("Mew ex", "#f85888", "Special illustration rare", 48.00),
    ("Misty's Determination", "#b8b8d0", "Uncommon", 0.30), ("Pikachu ex", "#f8d030", "Special illustration rare", 62.00),
]
OWNED = [0, 1, 3, 5, 6, 8, 9, 10, 12, 13, 14, 16, 18, 19, 20, 21, 22]


class DemoAPI:
    """Stands in for TCGdex with one made-up set."""
    lang = "en"

    def get_set(self, set_id):
        return {"id": SET_ID, "name": "Demo Waves", "releaseDate": "2026-03-27", "serie": {"name": "Demo"},
                "cardCount": {"official": 20, "total": len(CARDS)},
                "cards": [{"id": f"{SET_ID}-{i + 1:03d}", "localId": f"{i + 1:03d}", "name": c[0], "image": None}
                          for i, c in enumerate(CARDS)]}

    def get_card(self, card_id):
        i = int(card_id.rsplit("-", 1)[1]) - 1
        name, _, rarity, price = CARDS[i]
        holo = rarity not in ("Common", "Uncommon")
        key = "holofoil" if holo else "normal"
        tcg = {key: {"marketPrice": price, "lowPrice": round(price * 0.8, 2), "midPrice": round(price * 1.1, 2)}}
        if not holo:
            tcg["reverse-holofoil"] = {"marketPrice": round(price * 2.5 + 0.2, 2)}
        return {"id": card_id, "name": name, "rarity": rarity,
                "variants": {"normal": not holo, "reverse": not holo, "holo": holo},
                "pricing": {"tcgplayer": {"unit": "USD", **tcg}}}


def picture(i: int, folder: Path) -> str:
    """A simple drawn card: a coloured frame, the name and a big number. No real artwork."""
    name, colour, rarity, _ = CARDS[i]
    im = Image.new("RGB", (286, 400), colour)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([10, 10, 276, 390], 14, fill="#fdf8e8")
    d.rounded_rectangle([22, 54, 264, 230], 8, fill=colour)
    try:
        big = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
        huge = ImageFont.truetype("DejaVuSans-Bold.ttf", 64)
        small = ImageFont.truetype("DejaVuSans.ttf", 15)
    except OSError:
        big = huge = small = ImageFont.load_default()
    d.text((22, 20), name, fill="#1f1530", font=big)
    d.text((143, 142), f"{i + 1}", fill="#fdf8e8", font=huge, anchor="mm")
    d.text((22, 250), rarity, fill="#4a4060", font=small)
    d.text((22, 360), f"Demo Waves · {i + 1:03d}/020", fill="#4a4060", font=small)
    file = hashlib.md5(f"demo-{i + 1:03d}".encode()).hexdigest() + ".jpg"  # saved pictures have 32-hex names
    folder.mkdir(parents=True, exist_ok=True)
    im.save(folder / file, quality=88)
    return file


def main() -> None:
    conn = web.conn
    from app import startup
    startup.before_first_page(conn, web.BACKUPS)  # the database set up, as the app's start does
    if auth.user_count(conn):
        sys.exit("This data folder already has accounts; use a new one.")
    with db.transaction(conn):
        for site in sites.SITES:
            sites.set_on(conn, site, False)  # a demo asks nobody
    user = auth.create_user(conn, "misty", "pikachu123", is_admin=True)
    api = DemoAPI()
    catalog.cache_set(conn, api, SET_ID)
    rng = random.Random(14)
    with db.transaction(conn):
        for i in range(len(CARDS)):
            card_id = f"{SET_ID}-{i + 1:03d}"
            conn.execute("UPDATE cards SET saved_picture = ?, saved_from = '' WHERE id = ?",
                         (picture(i, card_pictures.FOLDER), card_id))
        for i in OWNED:
            card_id = f"{SET_ID}-{i + 1:03d}"
            variant = "holo" if CARDS[i][2] not in ("Common", "Uncommon") else rng.choice(["normal", "reverse"])
            inventory.set_quantity(conn, user, card_id, variant, "NM", rng.choice([1, 1, 1, 2, 3]), by=user,
                                   origin=rng.choice(["pack", "pack", "trade", "single"]))
            prices.store(conn, card_id, api.get_card(card_id))
    try:
        spending.create_purchase(conn, user, "Demo Waves booster box", 129.99, packs=36)
    except Exception as e:  # the demo works without it
        print("no purchase:", e)
    conn.commit()
    print(f"Demo collection ready: {len(OWNED)} cards owned. Sign in as misty / pikachu123.")


if __name__ == "__main__":
    main()
