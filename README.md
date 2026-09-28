# ws2yahoo

Mirror your Wealthsimple trades into a Yahoo Finance portfolio, so Yahoo
always shows what you actually own.

```
$ ws2yahoo sync
3 trade(s) to mirror into p_1 since 2026-09-21 (4 already synced):
  1. 20260923 sell         10 XEQT.TO     @ 36.12      [TFSA] order-00AbCdEf1234
  2. 20260925 buy          25 VFV.TO      @ 158.4      [RRSP] order-00GhIjKl5678
  ...
Record these 3 trade(s) into p_1? [y/N] y

$ ws2yahoo diff
p_1 matches Wealthsimple.
```

- Reads your Wealthsimple activity and holdings (read-only: it can't trade or move money).
- Records buys, sells, DRIP reinvestments and share journals (e.g. Norbert's gambit) in Yahoo, oldest first, adding tickers as needed.
- Remembers what it already mirrored, so running it again never double-counts.
- `diff` compares share counts on both sides, so you can confirm the mirror is exact.
- Built to be driven by an AI agent: every command has `--json` output, and there's a ready-made [agent skill](skills/ws2yahoo/SKILL.md).

## Install

Requires Python 3.9+.

```bash
uv tool install git+https://github.com/Hankyone/ws2yahoo
# or: pipx install git+https://github.com/Hankyone/ws2yahoo
```

## Set up

ws2yahoo has no passwords of its own. It borrows the logins from your
everyday Chrome (Brave, Edge and Chromium work too).

1. In Chrome, open `chrome://inspect/#remote-debugging` and turn on remote debugging.
2. Log in to [finance.yahoo.com](https://finance.yahoo.com) and [my.wealthsimple.com](https://my.wealthsimple.com).
3. Connect, then pick the Yahoo portfolio to mirror into:

```bash
ws2yahoo auth          # Chrome asks you to allow the connection: click Allow
ws2yahoo portfolios
ws2yahoo config --default-portfolio p_1
```

4. Run the first sync from the date your Yahoo portfolio stops being accurate
   (or from when you opened Wealthsimple, for an empty portfolio):

```bash
ws2yahoo sync --since 2026-01-01 --dry-run   # preview
ws2yahoo sync --since 2026-01-01             # record
ws2yahoo diff                                # check
```

After that, `ws2yahoo sync` picks up where the last sync ended.

If some of those trades are already in Yahoo because you entered them by
hand, pass their ids (shown in the preview) with `--skip ID,ID` so they aren't
recorded twice.

## Use it with your AI agent

Copy the skill into your agent's skills folder, e.g. for Claude Code:

```bash
mkdir -p ~/.claude/skills && cp -r skills/ws2yahoo ~/.claude/skills/
```

Then ask things like "sync my Yahoo portfolio with Wealthsimple" or "is my
Yahoo portfolio up to date?". Codex and other agents can read the same
[SKILL.md](skills/ws2yahoo/SKILL.md).

## Commands

| Command | What it does |
| --- | --- |
| `auth` | Read your Yahoo and Wealthsimple logins from Chrome |
| `config` | Set the default portfolio (`--default-portfolio`) and symbol overrides (`--map GOLD=GC=F`) |
| `sync` | Mirror Wealthsimple trades into Yahoo (`--since`, `--account`, `--skip`, `--dry-run`, `-y`) |
| `diff` | List symbols whose share counts differ between Wealthsimple and Yahoo |
| `activity` | Show the raw Wealthsimple activity feed (`--since`, `--type`, `--account`) |
| `portfolios`, `holdings` | Show Yahoo portfolios and lots |
| `add`, `delete-lot`, `delete-position` | Edit the Yahoo portfolio by hand |

Global flags: `--portfolio ID` (instead of the default) and `--json`.

## Good to know

- **Symbols.** Wealthsimple tickers are translated using their exchange:
  `CASH` on the TSX becomes `CASH.TO`, `HISU.U` becomes `HISU-U.TO`, `BRK.B`
  becomes `BRK-B`. Anything without an exchange (like Wealthsimple's gold)
  needs an override: `ws2yahoo config --map GOLD=GC=F`.
- **Prices** are Wealthsimple's total value divided by shares, the same
  average price shown in fill emails. Dates are Toronto time.
- **Not mirrored:** options and corporate actions (splits). `sync` lists them
  so you can record them by hand.
- **Share journals** (e.g. `DLR.U` → `DLR`) are recorded as a sell of one
  listing at its purchase price and a buy of the other, so the conversion
  itself books no gain.
- **Logins.** Your Yahoo login lasts a long time; Wealthsimple's lasts about 30
  minutes. When one expires, ws2yahoo reads a fresh one from Chrome, and
  Chrome asks you to allow the connection again. If Wealthsimple has signed
  you out, log in again in Chrome.
- **Local files** live in `~/.config/ws2yahoo` (override with `WS2YAHOO_HOME`).
  Login files are readable only by you.

## How it works

Both sites' web apps talk to private APIs, and ws2yahoo calls the same ones:
Wealthsimple's GraphQL API at `my.wealthsimple.com/graphql` (read-only
queries) and Yahoo Finance's portfolio endpoints on
`query1.finance.yahoo.com`. See [AGENTS.md](AGENTS.md) for the details.

## Disclaimer

Unofficial and unaffiliated with Wealthsimple or Yahoo. It relies on
undocumented APIs that can change without notice. Check the results with
`ws2yahoo diff`, and don't rely on it for anything critical.

## License

MIT
