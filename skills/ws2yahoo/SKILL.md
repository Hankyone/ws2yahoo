---
name: ws2yahoo
description: Keep a Yahoo Finance portfolio in sync with the user's Wealthsimple account using the ws2yahoo CLI. Use when the user asks to sync, mirror, update or check their Yahoo portfolio against Wealthsimple, look up Wealthsimple trades, dividends or activity, or fix differences between the two.
---

# ws2yahoo

`ws2yahoo` mirrors Wealthsimple trades into a Yahoo Finance portfolio. Add
`--json` to any command for machine-readable output. Install it with
`uv tool install git+https://github.com/Hankyone/ws2yahoo` if `ws2yahoo` isn't
on PATH.

## Sync the portfolio

1. Preview: `ws2yahoo --json sync --dry-run`
   - Without a previous sync it exits asking for `--since YYYY-MM-DD`. Ask the
     user from which date their Yahoo portfolio is out of date.
2. Show the user the pending trades (date, side, quantity, symbol, price,
   account) and any `not_mirrored` items. Get their approval before recording.
3. Record: `ws2yahoo sync -y` (same `--since` as the preview, if one was needed).
4. Verify: `ws2yahoo --json diff`. `in_sync: true` means every share count matches.

If the user already entered some of the previewed trades in Yahoo by hand,
pass their ids with `--skip ID,ID` instead of recording them again.

## Check whether it's up to date

`ws2yahoo --json diff` lists every symbol whose share count differs. To
explain a difference, look at recent activity for that symbol:
`ws2yahoo --json activity --since YYYY-MM-DD --type DIY_BUY,DIY_SELL,JOURNAL_SHARES`.

## Other questions

- Wealthsimple activity (dividends, deposits, withholding tax, ...):
  `ws2yahoo --json activity --since YYYY-MM-DD [--type DIVIDEND,NON_RESIDENT_TAX] [--account tfsa]`
- Yahoo holdings and lots: `ws2yahoo --json holdings`
- Another Yahoo portfolio: add `--portfolio ID` (list them with `ws2yahoo portfolios`).

## When something fails

- Browser errors: remote debugging must be on at `chrome://inspect/#remote-debugging`,
  and the user must click **Allow** when Chrome asks. Tell them this before retrying.
- "No ... login in the browser": the user must log in to that site in Chrome, then run `ws2yahoo auth`.
- A symbol Yahoo doesn't know: set an override with
  `ws2yahoo config --map WEALTHSIMPLE=YAHOO` (e.g. `GOLD=GC=F`), then sync again.
- `sync` stops at the first failed trade. Fix the cause and rerun: recorded
  trades are remembered and won't repeat.

## Rules

- Wealthsimple access is read-only; nothing here can trade or move money.
- Always preview and get approval before `sync -y`, `add`, `delete-lot` or `delete-position`.
- Never touch portfolios other than the one the user means.
