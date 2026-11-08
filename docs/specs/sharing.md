# Sharing

## Purpose

Let the household show a binder to friends without giving them a login: a private link plus a code for each
person, showing the cards and nothing else about the collection. Sharing is for showing what you own, never
for selling.

## Behaviour

### Turning sharing on

1. Each binder has a Share switch, off by default. On, the binder gets a random link, `/b/<token>`.
2. The link alone shows only "Enter your access code".
3. A code is made for one person or group, with a name of up to 40 characters ("Misty", "Card club"). It's
   eight letters and digits without look-alikes (no 0, O, 1, I or L), shown once as `K7QM-4ZPA`, and kept
   only as a salted scrypt hash. Typing it is forgiving about case, spaces and dashes.
4. A binder can have up to 50 working codes.
5. The binder's share panel lists its codes (name, when made, by whom) and a history of codes made and
   revoked, which can be cleared. Clearing the history leaves working codes alone.

### Visiting

6. A device that enters a working code gets a signed pass cookie for that one link, for 30 days.
7. Every request checks that the code and the link still stand, so Revoke (one code), turning sharing off,
   or New link (a new token; the old link stops) shuts a visitor out at once.
8. A visitor sees the binder's pages in the owner's layout and order, or a grid with search, filters (set,
   rarity, language, printing, price range, automatic tags) and sorts. Both show the binder's card count,
   total value and when prices were last updated.
9. A card opens on its own page within the shared link, with its picture, printing, quantity and price, and
   where the price came from (a TCGplayer price, a PriceCharting link, the owner's price).
10. Copies of the same card and printing in different conditions show as one line.
11. A link that isn't shared any more says "This binder isn't shared any more."

### What a visitor never sees

12. A visitor never sees: condition, notes, who added a card, what was paid, where a copy came from, other
    binders, chases, the household's own tags, any other page of the app, or the household's photos and
    scans. A card whose only saved picture is the owner's photo or scan shows a picture from the web or the
    Pokémon's sprite instead.
13. Visitor pages are sent with `noindex, nofollow`, no referrer and no caching.

### Wrong codes

14. Wrong codes and unknown links from one address are limited to 10 in 10 minutes; after that, that address
    waits before trying again.
15. A device that already has a working pass always gets in, even while its address is locked out (another
    device on the same Wi-Fi typing wrong codes, say).
16. The lockout is kept in memory, so a restart clears it.

## Rules and limits

- Sharing shows what you own; it never offers anything for sale or takes contact details.
- Only cards in a shared binder are reachable; a card id outside the binder isn't found.
- Codes are never stored or logged as typed.
- The pass is signed with the install's secret key, and codes are looked up by a tag keyed with it, so changing
  that key ends every pass and code.
- Shared pages may show pictures from the Chinese datasets; they're shown to friends, not republished.

## Interfaces

- Pages: `/b/{token}`, `/b/{token}/code`, `/b/{token}/card/{card_id}`, `/b/{token}/picture/{card_id}`; owner
  side: `/collection/binders/{binder_id}/share`, `/share/new`, `/codes`, `/codes/{code_id}/revoke`,
  `/codes/history/clear`.
- Code: `app/shared.py`, `app/routes/shared.py`, `app/auth.py` (`Lockout`), `app/binder_pages.py`.
- Tables: `binders` (share switch and token), `binder_codes`, `binder_code_log`.
- Related specs: [My Stash](my-stash.md), [prices](prices.md), [deploy](deploy.md) (the secret key and the
  proxy's visitor address).

## Open questions

- Sharing a chase list will reuse links and codes; what a visitor sees there isn't decided yet.
- Binders themselves (layouts, pages, order) have their own spec, `binders.md`, not written yet.
