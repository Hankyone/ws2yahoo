"""Command-line interface.

    ws2yahoo auth                               # read Yahoo + Wealthsimple logins from Chrome
    ws2yahoo portfolios                         # list Yahoo portfolios
    ws2yahoo config --default-portfolio p_1     # portfolio used when --portfolio is omitted
    ws2yahoo sync --since 2026-01-01 --dry-run  # preview trades to mirror
    ws2yahoo sync                               # mirror new trades (resumes where the last sync ended)
    ws2yahoo diff                               # compare Yahoo holdings with Wealthsimple
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Optional

from . import config, sync, wealthsimple, yahoo
from .browser import Browser, BrowserError
from .models import Transaction
from .wealthsimple import WealthsimpleError
from .yahoo import YahooError


def _json(args, obj) -> bool:
    """Print ``obj`` as JSON when --json is set. Returns True if it handled output."""
    if args.json:
        print(json.dumps(obj, indent=2, default=str))
    return args.json


def _csv(values: Optional[list[str]]) -> list[str]:
    """Flatten repeatable, comma-separated option values."""
    return [v.strip() for value in values or [] for v in value.split(",") if v.strip()]


def _pf(args) -> str:
    pf_id = args.portfolio or config.settings().get("portfolio")
    if not pf_id:
        sys.exit("No portfolio given. Pass --portfolio ID or set a default with "
                 "`ws2yahoo config --default-portfolio ID` (see `ws2yahoo portfolios`).")
    return pf_id


def _symbols() -> dict:
    return config.settings().get("symbols", {})


# -- setup ------------------------------------------------------------------------
def cmd_auth(args):
    with Browser() as browser:  # one connection, so Chrome asks for approval once
        yc = yahoo.login_from_browser(browser)
        wc = wealthsimple.login_from_browser(browser)
    token = config.load("wealthsimple.json")
    result = {"yahoo_portfolios": [p.pf_id for p in yc.list_portfolios()],
              "wealthsimple_accounts": [a["id"] for a in wc.accounts() if a["status"] == "open"],
              "wealthsimple_token_expires": datetime.fromtimestamp(token["expires_at"]).isoformat(timespec="minutes"),
              "saved_to": str(config.HOME)}
    if _json(args, result):
        return
    print(f"Yahoo: signed in, {len(result['yahoo_portfolios'])} portfolio(s).")
    print(f"Wealthsimple: signed in, {len(result['wealthsimple_accounts'])} open account(s).")
    print(f"Saved to {config.HOME}")


def cmd_config(args):
    settings = config.settings()
    if args.default_portfolio:
        settings["portfolio"] = args.default_portfolio
    for pair in args.map or []:
        ws_sym, sep, yahoo_sym = pair.partition("=")
        if not sep or not ws_sym or not yahoo_sym:
            sys.exit(f"--map expects WEALTHSIMPLE=YAHOO, got {pair!r}")
        settings.setdefault("symbols", {})[ws_sym] = yahoo_sym
    for ws_sym in args.unmap or []:
        settings.get("symbols", {}).pop(ws_sym, None)
    if args.default_portfolio or args.map or args.unmap:
        config.save("config.json", settings)
    if _json(args, settings):
        return
    print(f"default portfolio: {settings.get('portfolio') or '(none)'}")
    for ws_sym, yahoo_sym in sorted(settings.get("symbols", {}).items()):
        print(f"symbol: {ws_sym} -> {yahoo_sym}")


# -- Yahoo ------------------------------------------------------------------------
def cmd_portfolios(args):
    pfs = yahoo.get_client().list_portfolios()
    if _json(args, [{"pf_id": p.pf_id, "name": p.name, "base_currency": p.base_currency} for p in pfs]):
        return
    default = config.settings().get("portfolio")
    for p in pfs:
        print(f"{p.pf_id}\t{p.name}\t{p.base_currency}" + ("\t(default)" if p.pf_id == default else ""))


def cmd_holdings(args):
    pf = yahoo.get_client().get_portfolio(_pf(args))
    positions = sorted((p for p in pf.positions if args.all or p.has_holdings), key=lambda p: p.symbol)
    if _json(args, {"pf_id": pf.pf_id, "name": pf.name, "base_currency": pf.base_currency,
                    "positions": [asdict(p) for p in positions]}):
        return
    print(f"# {pf.name} ({pf.pf_id})  base={pf.base_currency}")
    for pos in positions:
        if not pos.has_holdings:
            print(f"{pos.symbol}\t(tracked only)\tposId={pos.pos_id}")
            continue
        print(f"\n{pos.symbol}\tqty={pos.quantity}\tposId={pos.pos_id}")
        for lot in pos.lots:
            print(f"  {lot.lot_id}\t{lot.quantity}\t@{lot.purchase_price}\t{lot.trade_date}"
                  + (f"\t# {lot.comment}" if lot.comment else ""))


def cmd_add(args):
    tx = Transaction(symbol=args.symbol, quantity=args.qty, price=args.price, trade_date=args.date.replace("-", ""),
                     side="sell" if args.sell else "buy", commission=args.commission, comment=args.comment)
    pf_id = _pf(args)
    yc = yahoo.get_client()
    if not yc.get_portfolio(pf_id).find_position(tx.symbol):
        yc.add_position(pf_id, tx.symbol)
    tx_id = yc.add_transaction(pf_id, tx)
    if _json(args, {**asdict(tx), "transaction_id": tx_id}):
        return
    print(f"Recorded {tx.side} {tx.quantity} {tx.symbol} @ {tx.price} ({tx.trade_date}) -> {tx_id}")


def cmd_delete_lot(args):
    yahoo.get_client().delete_lot(_pf(args), args.lot_id, args.symbol)
    if not _json(args, {"deleted": args.lot_id}):
        print(f"Deleted lot {args.lot_id} ({args.symbol})")


def cmd_delete_position(args):
    yahoo.get_client().delete_position(_pf(args), args.pos_id)
    if not _json(args, {"deleted": args.pos_id}):
        print(f"Deleted position {args.pos_id} and its transactions")


# -- Wealthsimple -----------------------------------------------------------------
def cmd_activity(args):
    wc = wealthsimple.get_client()
    items = wc.activities(args.since, args.until, types=_csv(args.type) or None,
                          account_ids=wc.resolve_accounts(_csv(args.account)), limit=args.limit)
    if _json(args, items):
        return
    labels = {a["id"]: a["label"] for a in wc.accounts()}
    for it in items:
        what = it["type"] + (f"/{it['subType']}" if it.get("subType") else "")
        asset = f"{float(it['assetQuantity']):g} {it['assetSymbol']}" if it.get("assetQuantity") else ""
        amount = f"{it['amount']} {it['currency']}" if it.get("amount") else ""
        print(f"{wealthsimple.trade_date(it['occurredAt'])}  {labels.get(it['accountId'], it['accountId']):<14} "
              f"{what:<32} {asset:<22} {amount:<18} {it['unifiedStatus']}  {it['canonicalId']}")


def cmd_sync(args):
    pf_id = _pf(args)
    since = args.since or sync.resume_date(pf_id)
    if not since:
        sys.exit("First sync for this portfolio: pass --since YYYY-MM-DD (the first trade date to mirror).")
    wc = wealthsimple.get_client()
    txs, unsupported = sync.trades(wc, since, args.until, wc.resolve_accounts(_csv(args.account)), _symbols())
    skip = set(_csv(args.skip))
    done = set(sync.synced(pf_id)) | skip
    pending = [t for t in txs if t.external_id not in done]
    yc = yahoo.get_client()
    tracked = {p.symbol for p in yc.get_portfolio(pf_id).positions}
    new_tickers = sorted({t.symbol for t in pending} - tracked)
    not_mirrored = [{"id": it["canonicalId"], "type": it["type"], "symbol": it["assetSymbol"],
                     "date": wealthsimple.trade_date(it["occurredAt"])} for it in unsupported]
    report = {"portfolio": pf_id, "since": since, "pending": [asdict(t) for t in pending],
              "already_synced": len(txs) - len(pending), "new_tickers": new_tickers, "not_mirrored": not_mirrored}

    if not args.json:
        print(f"{len(pending)} trade(s) to mirror into {pf_id} since {since} "
              f"({len(txs) - len(pending)} already synced):")
        for i, t in enumerate(pending, 1):
            new = "  (new ticker)" if t.symbol in new_tickers else ""
            print(f"  {i}. {t.trade_date} {t.side:4s} {t.quantity:>10g} {t.symbol:<11} @ {t.price:<10} "
                  f"[{t.account}] {t.external_id}{new}")
        if not_mirrored:
            print("\nNot mirrored automatically (record by hand if needed):")
            for it in not_mirrored:
                print(f"  {it['date']} {it['type']:<16} {it['symbol'] or '':<10} {it['id']}")
    if args.dry_run or not pending:
        if not _json(args, {**report, "dry_run": args.dry_run, "recorded": 0}) and args.dry_run:
            print("\n--dry-run: no changes made.")
        return
    if not args.yes and not args.json:
        if input(f"\nRecord these {len(pending)} trade(s) into {pf_id}? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return

    dates = {t.external_id: t.trade_date for t in txs}
    for external_id in skip:
        sync.mark_synced(pf_id, external_id, dates.get(external_id, ""))
    recorded, error = [], None
    try:
        for tx, tx_id in sync.apply(yc, pf_id, pending):
            recorded.append({"external_id": tx.external_id, "symbol": tx.symbol, "transaction_id": tx_id})
            if not args.json:
                print(f"  OK   {tx.side} {tx.quantity:g} {tx.symbol} @ {tx.price} -> {tx_id}")
    except YahooError as e:
        error = str(e)
    if _json(args, {**report, "recorded": len(recorded), "results": recorded, "error": error}):
        return
    print(f"\nDone: {len(recorded)} of {len(pending)} recorded.")
    if error:
        sys.exit(f"Stopped at trade {len(recorded) + 1}: {error}\nFix it and run sync again; "
                 "trades already recorded won't be repeated.")


def cmd_diff(args):
    pf_id = _pf(args)
    wc = wealthsimple.get_client()
    rows = sync.diff(wc.positions(wc.resolve_accounts(_csv(args.account))),
                     yahoo.get_client().get_portfolio(pf_id), _symbols())
    if _json(args, {"portfolio": pf_id, "in_sync": not rows, "mismatches": rows}):
        return
    if not rows:
        print(f"{pf_id} matches Wealthsimple.")
        return
    print(f"{'symbol':<12}{'wealthsimple':>14}{'yahoo':>14}{'diff':>14}")
    for r in rows:
        print(f"{r['symbol']:<12}{r['wealthsimple']:>14}{r['yahoo']:>14}{r['diff']:>+14}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ws2yahoo", description="Mirror your Wealthsimple trades into Yahoo Finance.")
    p.add_argument("-p", "--portfolio", help="Yahoo portfolio id (default: `ws2yahoo config --default-portfolio`)")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    def cmd(name, func, help):
        sp = sub.add_parser(name, help=help, description=help)
        sp.set_defaults(func=func)
        return sp

    cmd("auth", cmd_auth, "Read your Yahoo and Wealthsimple logins from Chrome.")

    c = cmd("config", cmd_config, "Show or change settings.")
    c.add_argument("--default-portfolio", metavar="ID", help="Portfolio to use when --portfolio is omitted.")
    c.add_argument("--map", action="append", metavar="WS=YAHOO",
                   help="Always map a Wealthsimple symbol to a Yahoo symbol, e.g. GOLD=GC=F. Repeatable.")
    c.add_argument("--unmap", action="append", metavar="WS", help="Remove a symbol mapping.")

    account_help = "Only these Wealthsimple accounts: ids or types (tfsa, fhsa, rrsp, non_registered...)."

    s = cmd("sync", cmd_sync, "Mirror Wealthsimple trades into the Yahoo portfolio.")
    s.add_argument("--since", help="YYYY-MM-DD (default: date of the last mirrored trade)")
    s.add_argument("--until", help="YYYY-MM-DD (inclusive)")
    s.add_argument("--account", action="append", help=account_help)
    s.add_argument("--skip", action="append", metavar="ID",
                   help="Activity id(s) already in Yahoo: mark as mirrored without recording.")
    s.add_argument("--dry-run", action="store_true", help="Show what would be recorded.")
    s.add_argument("-y", "--yes", action="store_true", help="Record without asking.")

    d = cmd("diff", cmd_diff, "Compare Yahoo holdings with Wealthsimple positions.")
    d.add_argument("--account", action="append", help=account_help)

    a = cmd("activity", cmd_activity, "List the Wealthsimple activity feed.")
    a.add_argument("--since", default=(date.today() - timedelta(days=30)).isoformat(),
                   help="YYYY-MM-DD (default: 30 days ago)")
    a.add_argument("--until", help="YYYY-MM-DD (inclusive)")
    a.add_argument("--type", action="append", help="Activity types, e.g. DIY_BUY,DIY_SELL,DIVIDEND.")
    a.add_argument("--account", action="append", help=account_help)
    a.add_argument("--limit", type=int, help="Stop after this many items.")

    cmd("portfolios", cmd_portfolios, "List your Yahoo portfolios.")

    h = cmd("holdings", cmd_holdings, "Show the Yahoo portfolio's holdings and lots.")
    h.add_argument("--all", action="store_true", help="Include tickers with no holdings.")

    ad = cmd("add", cmd_add, "Record one buy or sell in the Yahoo portfolio.")
    ad.add_argument("--symbol", required=True, help="Yahoo symbol, e.g. SHOP.TO")
    ad.add_argument("--qty", type=float, required=True)
    ad.add_argument("--price", type=float, required=True)
    ad.add_argument("--date", required=True, help="YYYY-MM-DD")
    ad.add_argument("--sell", action="store_true", help="Record a sell instead of a buy.")
    ad.add_argument("--commission", type=float, default=0.0)
    ad.add_argument("--comment", default="")

    dl = cmd("delete-lot", cmd_delete_lot, "Delete one lot from the Yahoo portfolio.")
    dl.add_argument("lot_id")
    dl.add_argument("--symbol", required=True)

    dp = cmd("delete-position", cmd_delete_position, "Remove a ticker and its transactions.")
    dp.add_argument("pos_id")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (YahooError, WealthsimpleError, BrowserError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
