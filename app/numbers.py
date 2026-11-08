"""Card number parsing.

Card numbers are not always integers ('TG05', 'GG01', 'SV049', '177a'), so
SQL like CAST(number AS INT) silently turns subset cards into 0 and counts
them as main-set cards. Everything is worked out once here, when a set is
cached, and stored as sort_number / is_main_set.
"""

import re

_PLAIN = re.compile(r"^0*(\d+)$")
_DIGITS = re.compile(r"(\d+)")


def parse_number(number: str, printed_total: int) -> tuple[int, bool]:
    """Return (sort_number, is_main_set) for a printed card number.

    A card is in the main set only when its number is a plain integer no
    larger than the set's printed total. Prefixed subset numbers (TG05) and
    lettered numbers (177a) are never main-set cards.
    """
    number = number.strip()
    plain = _PLAIN.match(number)
    if plain:
        value = int(plain.group(1))
        return value, value <= printed_total
    digits = _DIGITS.search(number)
    # Subsets sort after the main run and any numbered secret rares.
    sort_number = 10_000 + int(digits.group(1)) if digits else 99_999
    return sort_number, False
