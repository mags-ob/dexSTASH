"""'Looks like' words for Describe: what a Pokémon is, read from its Simplified Chinese and
Japanese names, which often say it plainly. Maushold is 一家鼠 ("family mouse") and イッカネズミ (ネズミ,
"mouse"), while PokéAPI calls it the Family Pokémon and its Pokédex text never says mouse.

Plain lookups, no AI. Known false friends are taken out first (地鼠 is a mole, not a mouse).
Bulbapedia's "Origin" text (bulbapedia.py) and your own words add more on top."""

# Simplified Chinese characters (pokemon_species_names language 12) -> English words
ZH = {
    "鼠": "mouse", "猫": "cat", "喵": "cat", "狗": "dog", "犬": "dog", "狐": "fox", "狼": "wolf", "熊": "bear",
    "猴": "monkey", "猿": "ape", "龟": "turtle", "龙": "dragon", "鳄": "crocodile", "蛇": "snake", "蛙": "frog",
    "蟾": "toad", "鸟": "bird", "雀": "sparrow", "鸭": "duck", "鹅": "goose", "鹰": "hawk", "雕": "eagle",
    "鸮": "owl", "枭": "owl", "鸡": "chicken", "鸽": "pigeon", "鹦": "parrot", "鹤": "crane", "鹈": "pelican",
    "燕": "swallow", "企鹅": "penguin", "鱼": "fish", "鲤": "carp", "鲨": "shark", "鲸": "whale", "鲶": "catfish",
    "虾": "shrimp", "蟹": "crab", "贝": "shell", "蚌": "clam", "螺": "snail", "蜗": "snail", "章鱼": "octopus",
    "乌贼": "squid", "水母": "jellyfish", "海星": "starfish", "海马": "seahorse", "海豹": "seal", "海狮": "sea lion",
    "虫": "bug", "蝶": "butterfly", "蛾": "moth", "蜂": "bee", "蚁": "ant", "蛛": "spider", "蝎": "scorpion",
    "螳": "mantis", "蜻": "dragonfly", "蝠": "bat", "蝙": "bat", "蚯": "worm", "蛞": "slug",
    "马": "horse", "驹": "pony", "牛": "bull", "羊": "sheep", "鹿": "deer", "猪": "pig", "豚": "pig", "兔": "rabbit",
    "象": "elephant", "狮": "lion", "虎": "tiger", "豹": "leopard", "獾": "badger", "鼬": "weasel", "貂": "weasel",
    "獭": "otter", "狸": "raccoon", "刺猬": "hedgehog", "松鼠": "squirrel", "鼹": "mole", "蜥": "lizard",
    "蜴": "lizard", "恐龙": "dinosaur", "树": "tree", "花": "flower", "草": "grass", "叶": "leaf", "藤": "vine",
    "菇": "mushroom", "蘑": "mushroom", "种子": "seed", "果": "fruit", "仙人掌": "cactus", "石": "rock", "岩": "rock",
    "钢": "steel", "冰": "ice", "雪": "snow", "火": "fire", "炎": "fire", "水": "water", "电": "electric",
    "雷": "thunder", "鬼": "ghost", "幽灵": "ghost", "灵": "spirit", "偶": "doll", "娃娃": "doll", "壶": "pot",
    "茶": "tea", "剑": "sword", "盾": "shield", "铃": "bell", "钥匙": "key", "磁": "magnet", "球": "ball",
    "蛋": "egg", "星": "star", "月": "moon", "日": "sun", "太阳": "sun", "云": "cloud", "风": "wind", "沙": "sand",
    "泥": "mud", "宝石": "gem", "钻": "diamond", "糖": "candy", "奶": "milk", "冰淇淋": "ice cream",
    "王": "king", "后": "queen", "骑士": "knight", "武士": "samurai", "忍": "ninja", "拳": "fist",
}

# Japanese katakana (languages 1 and 11) -> English words; longer entries are checked first
JA = {
    "ネズミ": "mouse", "ネコ": "cat", "ニャ": "cat", "イヌ": "dog", "キツネ": "fox", "オオカミ": "wolf", "クマ": "bear",
    "サル": "monkey", "カメ": "turtle", "ガメ": "turtle", "ドラゴン": "dragon", "ワニ": "crocodile", "ヘビ": "snake",
    "カエル": "frog", "ガエル": "frog", "トリ": "bird", "スズメ": "sparrow", "アヒル": "duck", "カモ": "duck",
    "フクロウ": "owl", "ハト": "pigeon", "ペンギン": "penguin", "サカナ": "fish", "コイ": "carp", "サメ": "shark",
    "クジラ": "whale", "エビ": "shrimp", "カニ": "crab", "ガニ": "crab", "タコ": "octopus", "イカ": "squid",
    "クラゲ": "jellyfish", "ヒトデ": "starfish", "ムシ": "bug", "チョウ": "butterfly", "ハチ": "bee", "バチ": "bee",
    "アリ": "ant", "クモ": "spider", "グモ": "spider", "サソリ": "scorpion", "カマキリ": "mantis", "コウモリ": "bat",
    "ウマ": "horse", "ウシ": "bull", "ヒツジ": "sheep", "シカ": "deer", "ジカ": "deer", "ブタ": "pig", "ウサギ": "rabbit",
    "ゾウ": "elephant", "ライオン": "lion", "トラ": "tiger", "ドラ": "dragon", "キノコ": "mushroom", "ハナ": "flower",
    "ツボ": "pot", "カギ": "key", "タマゴ": "egg", "ホシ": "star", "ボシ": "star", "ユキ": "snow", "ゴースト": "ghost",
    "ニンジャ": "ninja", "ナイト": "knight",
}

# Names where a root says the wrong thing: taken out before reading the roots.
FALSE_FRIENDS = {
    "地鼠": "mole",        # Diglett and Dugtrio: 'earth mouse' is a mole
    "鼹鼠": "mole",
    "老鼠": "mouse",
    "蝙蝠": "bat",         # 蝠 alone is already bat; keeps 'mouse' out of 'bat'
    "乌贼": "squid",       # 'black thief' is a squid
    "海马": "seahorse",    # not a horse
    "海狮": "sea lion",
    "海豹": "seal",
    "海星": "starfish",
    "水母": "jellyfish",
    "章鱼": "octopus",
    "企鹅": "penguin",
    "松鼠": "squirrel",
    "刺猬": "hedgehog",
    "恐龙": "dinosaur",
    "仙人掌": "cactus",
    "冰淇淋": "ice cream",
    "幽灵": "ghost",
    "太阳": "sun",
    "种子": "seed",
    "宝石": "gem",
    "钥匙": "key",
    "娃娃": "doll",
    "骑士": "knight",
    "武士": "samurai",
}
# Words never given: they say more about type than looks, and are already Describe's type chips.
_SKIP = {"fire", "water", "electric", "grass", "ice", "steel", "ghost"}

VOCABULARY = sorted({w for w in [*ZH.values(), *JA.values(), *FALSE_FRIENDS.values()] if w not in _SKIP} | {
    "dog", "puppy", "kitten", "lizard", "hamster", "squirrel", "rodent", "raccoon", "panda", "koala", "kangaroo",
    "hippopotamus", "rhinoceros", "gorilla", "chimpanzee", "lemur", "sloth", "anteater", "armadillo", "platypus",
    "seahorse", "eel", "jellyfish", "anglerfish", "pufferfish", "stingray", "manta", "coral", "anemone", "urchin",
    "lobster", "hermit crab", "barnacle", "oyster", "mussel", "nautilus", "ammonite", "trilobite", "dinosaur",
    "pterosaur", "pterodactyl", "tyrannosaurus", "stegosaurus", "triceratops", "mammoth", "dodo", "hummingbird",
    "woodpecker", "toucan", "flamingo", "peacock", "ostrich", "vulture", "falcon", "seagull", "albatross", "phoenix",
    "griffin", "unicorn", "pegasus", "kitsune", "tanuki", "kappa", "tengu", "oni", "yokai", "golem", "gargoyle",
    "mermaid", "fairy", "witch", "wizard", "clown", "jester", "mummy", "skeleton", "zombie", "vampire", "angel",
    "robot", "ufo", "alien", "teapot", "kettle", "lamp", "lantern", "candle", "chandelier", "umbrella", "scarf",
    "hat", "crown", "anchor", "trash", "garbage", "keychain", "coin", "magnet", "bell", "drum", "guitar", "toy",
    "doll", "puppet", "balloon", "kite", "cloud", "volcano", "boulder", "pebble", "crystal", "pearl", "seed",
    "acorn", "pinecone", "cactus", "tulip", "rose", "lily", "lotus", "cherry", "apple", "banana", "coconut",
    "pineapple", "pepper", "onion", "lettuce", "cabbage", "pumpkin", "gourd", "jalapeño", "cotton", "dandelion",
    "pine", "palm", "bamboo", "shiitake", "sushi", "rice", "bread", "cake", "donut", "pastry", "lollipop",
})


def words_for(zh_name: str = "", ja_names: tuple[str, ...] = ()) -> list[str]:
    """English 'looks like' words from a Pokémon's Chinese and Japanese names."""
    out: list[str] = []
    text = zh_name or ""
    for phrase in sorted(FALSE_FRIENDS, key=len, reverse=True):
        if phrase in text:
            out.append(FALSE_FRIENDS[phrase])
            text = text.replace(phrase, " ")
    for root in sorted(ZH, key=len, reverse=True):
        if root in text:
            out.append(ZH[root])
            text = text.replace(root, " ")
    for name in ja_names:
        rest = name or ""
        for root in sorted(JA, key=len, reverse=True):
            if root in rest:
                out.append(JA[root])
                rest = rest.replace(root, " ")
    return [w for w in dict.fromkeys(out) if w not in _SKIP]


def from_text(text: str, limit: int = 8) -> list[str]:
    """Looks-like words found in a passage (Bulbapedia's Origin), in the order they first appear."""
    import re
    low = " " + re.sub(r"[^a-z\- ]", " ", (text or "").lower()) + " "
    found = []
    for w in VOCABULARY:
        for form in (w, w + "s", w + "es"):
            i = low.find(f" {form} ")
            if i >= 0:
                found.append((i, w))
                break
    return [w for _, w in sorted(found)][:limit]
