"""Turn Wealthsimple activity into Yahoo transactions, apply them, and check the result."""
from __future__ import annotations

from datetime import datetime
from typing import Iterator, Optional

from . import config
from .models import Portfolio, Transaction
from .wealthsimple import WealthsimpleClient, WealthsimpleError, trade_date
from .yahoo import YahooClient

TRADE_TYPES = ["DIY_BUY", "DIY_SELL", "JOURNAL_SHARES"]
# Trade-like activity we can't mirror automatically; reported instead of dropped.
UNSUPPORTED_TYPES = ["OPTIONS_BUY", "OPTIONS_SELL", "OPTIONS_EXPIRY", "CORPORATE_ACTION"]

_ORDER_LABEL = {
    "MARKET_ORDER": "Market",
    "LIMIT_ORDER": "Limit",
    "STOP_LIMIT_ORDER": "Stop limit",
    "FRACTIONAL_ORDER": "Fractional",
    "DIVIDEND_REINVESTMENT": "DRIP",
}
# Yahoo suffixes for non-US listings. US listings use the bare symbol.
_EXCHANGE_SUFFIX = {"TSX": ".TO", "TSX-V": ".V", "CBOE CANADA": ".NE", "NEO": ".NE", "CSE": ".CN"}


# -- symbols ------------------------------------------------------------------
def yahoo_symbol(symbol: str, exchange: Optional[str]) -> str:
    """Wealthsimple symbol + listing exchange -> Yahoo symbol.

    ``CASH`` on TSX -> ``CASH.TO``, ``HISU.U`` on TSX -> ``HISU-U.TO``,
    ``BRK.B`` on NYSE -> ``BRK-B``. Securities without an exchange (e.g.
    Wealthsimple's precious metals) are returned unchanged.
    """
    if not exchange:
        return symbol
    return symbol.replace(".", "-") + _EXCHANGE_SUFFIX.get(exchange.upper(), "")


def map_symbol(symbol: str, exchange: Optional[str], overrides: Optional[dict] = None) -> str:
    return (overrides or {}).get(symbol) or yahoo_symbol(symbol, exchange)


def security_symbol(security: dict, overrides: Optional[dict] = None) -> str:
    stock = security.get("stock") or {}
    return map_symbol(stock.get("symbol") or security["id"], stock.get("primaryExchange"), overrides)


# -- activity -> transactions ---------------------------------------------------
def trades(
    client: WealthsimpleClient,
    since: str,
    until: Optional[str] = None,
    account_ids: Optional[list[str]] = None,
    overrides: Optional[dict] = None,
) -> tuple[list[Transaction], list[dict]]:
    """Completed Wealthsimple trades as Yahoo transactions, oldest first.

    Returns ``(transactions, unsupported)``; ``unsupported`` lists trade-like
    activity that can't be mirrored automatically (options, splits...).
    """
    labels = {a["id"]: a["label"] for a in client.accounts()}
    items = client.activities(since, until, TRADE_TYPES + UNSUPPORTED_TYPES, account_ids,
                              statuses=["COMPLETED"], oldest_first=True)
    txs: list[Transaction] = []
    unsupported: list[dict] = []
    for item in items:
        account = labels.get(item["accountId"], item["accountId"])
        if item["type"] in ("DIY_BUY", "DIY_SELL"):
            txs.append(trade_to_transaction(item, account, overrides))
        elif item["type"] == "JOURNAL_SHARES":
            prior = client.activities(until=item["occurredAt"], types=["DIY_BUY"], account_ids=[item["accountId"]],
                                      security_ids=[item["securityId"]], statuses=["COMPLETED"], limit=1)
            if not prior:
                raise WealthsimpleError(f"Can't price journal {item['canonicalId']}: no earlier buy of "
                                        f"{item['assetSymbol']} in {account}.")
            txs.extend(journal_to_transactions(item, prior[0], account, overrides))
        else:
            unsupported.append(item)
    return txs, unsupported


def _unit_price(item: dict) -> float:
    return round(float(item["amount"]) / float(item["assetQuantity"]), 4)


def trade_to_transaction(item: dict, account: str, overrides: Optional[dict] = None) -> Transaction:
    """A DIY_BUY/DIY_SELL item. Price is total value / shares, like the fill emails."""
    side = "buy" if item["type"] == "DIY_BUY" else "sell"
    order = _ORDER_LABEL.get(item.get("subType") or "", (item.get("subType") or "").title())
    symbol = (security_symbol(item["security"], overrides) if item.get("security")
              else map_symbol(item["assetSymbol"], None, overrides))
    return Transaction(
        symbol=symbol,
        quantity=float(item["assetQuantity"]),
        price=_unit_price(item),
        trade_date=trade_date(item["occurredAt"]),
        side=side,
        commission=float(item.get("fees") or 0),
        comment=f"Wealthsimple {order} {side.title()} {account}",
        account=account,
        external_id=item["canonicalId"],
    )


def journal_to_transactions(item: dict, prior_buy: dict, account: str,
                            overrides: Optional[dict] = None) -> list[Transaction]:
    """A share journal (e.g. DLR.U -> DLR) becomes a sell of one listing and a buy of the other.

    The feed only values the journal in the receiving listing's currency, so
    the sell leg reuses the last purchase price of the outgoing listing
    (``prior_buy``). That books no gain on the conversion itself.
    """
    qty = float(item["assetQuantity"])
    exchange = ((item.get("security") or {}).get("stock") or {}).get("primaryExchange")
    date = trade_date(item["occurredAt"])
    note = f"Wealthsimple journal {item['assetSymbol']} -> {item['counterAssetSymbol']} {account}"
    return [
        Transaction(symbol=map_symbol(item["assetSymbol"], exchange, overrides), quantity=qty,
                    price=_unit_price(prior_buy), trade_date=date, side="sell", comment=note, account=account,
                    external_id=f"{item['canonicalId']}:out"),
        Transaction(symbol=map_symbol(item["counterAssetSymbol"], exchange, overrides), quantity=qty,
                    price=round(float(item["amount"]) / qty, 4), trade_date=date, side="buy",
                    commission=float(item.get("fees") or 0), comment=note, account=account,
                    external_id=f"{item['canonicalId']}:in"),
    ]


# -- ledger of mirrored activity ------------------------------------------------
def synced(pf_id: str) -> dict[str, str]:
    """Wealthsimple activity ids already mirrored into ``pf_id``, with their trade dates."""
    return config.load("synced.json", {}).get(pf_id, {})


def mark_synced(pf_id: str, external_id: str, date: str = "") -> None:
    data = config.load("synced.json", {})
    data.setdefault(pf_id, {})[external_id] = date
    config.save("synced.json", data)


def resume_date(pf_id: str) -> Optional[str]:
    """``YYYY-MM-DD`` of the latest mirrored trade, where the next sync should start."""
    dates = [d for d in synced(pf_id).values() if d]
    return datetime.strptime(max(dates), "%Y%m%d").strftime("%Y-%m-%d") if dates else None


# -- apply & verify -----------------------------------------------------------------
def apply(yahoo: YahooClient, pf_id: str, pending: list[Transaction]) -> Iterator[tuple[Transaction, str]]:
    """Record ``pending`` in order, adding missing tickers. Yields ``(tx, transaction_id)``.

    Stops at the first failure (raising ``YahooError``): later sells may depend on it.
    """
    tracked = {p.symbol for p in yahoo.get_portfolio(pf_id).positions}
    for tx in pending:
        if tx.symbol not in tracked:
            yahoo.add_position(pf_id, tx.symbol)
            tracked.add(tx.symbol)
        tx_id = yahoo.add_transaction(pf_id, tx)
        mark_synced(pf_id, tx.external_id, tx.trade_date)
        yield tx, tx_id


def diff(ws_positions: list[dict], portfolio: Portfolio, overrides: Optional[dict] = None) -> list[dict]:
    """Symbols whose share count differs between Wealthsimple and the Yahoo portfolio."""
    ws_qty: dict[str, float] = {}
    for pos in ws_positions:
        sym = security_symbol(pos["security"], overrides)
        ws_qty[sym] = ws_qty.get(sym, 0.0) + float(pos["quantity"])
    yahoo_qty = {p.symbol: p.quantity for p in portfolio.positions if p.has_holdings}
    rows = []
    for sym in sorted(set(ws_qty) | set(yahoo_qty)):
        a, b = round(ws_qty.get(sym, 0.0), 4), round(yahoo_qty.get(sym, 0.0), 4)
        if a != b:
            rows.append({"symbol": sym, "wealthsimple": a, "yahoo": b, "diff": round(a - b, 4)})
    return rows

