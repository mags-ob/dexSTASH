# Core rules

These rules protect the people who run dexSTASH, the sources it takes data from, and the project's licence.
No project or personal rule may loosen them, and no task is a reason to work around them. If a change would
break one, stop and raise it on the issue.

Each rule says how it is checked. "The scan" means `tools/scan.sh`, which runs on your machine (a commit hook,
and before every push) using `tools/check_rules.py` and gitleaks, and reports what it finds. "Review" means a
person or assistant checks it while reviewing.

## 1. Nothing personal in the repository

- No real names, usernames, domains, IP addresses, server or device names, file paths from someone's machine,
  Docker network names or names of other apps on someone's server. This applies to code, comments, tests,
  docs, commit messages, issues and pull requests, since all of them are public.
- Commits are part of this. A merge pressed on github.com is written with the merger's profile name and e-mail,
  so whoever merges keeps their e-mail private (Settings › Emails) and a profile name they're happy to publish.
  Commit messages, pull requests and issues carry no links to pages only one person can open, such as an AI
  assistant's sessions or projects.
- Examples use the user `misty`, the domain `cards.example.com` and paths like `/srv/docker/dexstash`.
- Checked by: the scan (patterns such as home-server paths, private addresses and e-mail addresses, in every file
  and every commit's author, committer and message; the check never lists anyone's real details, since the check
  itself is public) and review.

## 2. No secrets

- No passwords, API keys, tokens, secret keys or `.env` files. A key a host enters in the app lives in their
  `data/` folder (`api_keys.json`), never in code, logs or backups.
- Never read or print a host's `.env` or `data/` files while helping them.
- Checked by: the scan (gitleaks scans every commit; a false alarm goes in `.gitleaks.toml`, matched by file and line, with a reason) and
  review.

## 3. Respect the data sources

- **Link out, don't scrape.** dexSTASH may link to public pages, but it never reads them. Pages on TCGplayer
  and PriceCharting are refused in `app/sites.py`, and that must stay true.
- **Data comes only from official APIs, open datasets or files a source publishes for reuse,** each listed in
  NOTICE.md with its terms. A source that needs a key uses the host's own key (for example CardTrader).
- **The app works for free.** No feature may require a paid service. A paid source is always optional.
- **Every outside request goes through `app/sites.py`.** It sends the honest User-Agent and honours the site
  switches. A new optional site starts switched off.
- **Downloaded data is never committed.** That covers card data, card pictures, sprites, Pokédex text,
  Bulbapedia text, the Chinese datasets, prices and models. NOTICE.md lists each source and its terms. Add a
  row there for any new source.
- Checked by: tests (`tests/test_core_rules.py` keeps the blocked sites blocked), the scan (refuses
  databases, the data folder, models and bundles) and review.

## 4. Licence and credit

- dexSTASH is AGPL-3.0. Don't add code or assets under a licence that can't be shipped with it.
- No artwork, logos or characters owned by someone else in the app's own assets. Card pictures are downloaded
  at run time, never shipped.
- Third-party code and fonts are listed in NOTICE.md with their licence.
- Checked by: review.

## 5. People's data stays theirs

- A database change never deletes or rewrites what someone typed, collected or uploaded without a backup
  first. The app already backs up before an upgrade, and a new database step must not get around that.
- Shared links show only what the owner chose to share.
- Checked by: tests and review.
