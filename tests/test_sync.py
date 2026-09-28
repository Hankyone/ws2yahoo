import pytest

from ws2yahoo import config, sync
from ws2yahoo.models import Lot, Portfolio, Position
from ws2yahoo.wealthsimple import to_utc, trade_date


def security(symbol, exchange):
    return {"id": "sec-s-1", "stock": {"symbol": symbol, "primaryExchange": exchange}}


def activity(**kw):
    item = {"canonicalId": "order-1", "accountId": "tfsa-1", "type": "DIY_BUY", "subType": "MARKET_ORDER",
            "occurredAt": "2026-07-08T17:48:00+00:00", "assetSymbol": "HISU.U", "assetQuantity": "200.0000000000",
            "amount": "20027.04", "fees": None, "security": security("HISU.U", "TSX")}
    item.update(kw)
    return item


@pytest.mark.parametrize("symbol, exchange, expected", [
    ("CASH", "TSX", "CASH.TO"),
    ("HISU.U", "TSX", "HISU-U.TO"),
    ("ABC", "TSX-V", "ABC.V"),
    ("XYZ", "CBOE CANADA", "XYZ.NE"),
    ("BRK.B", "NYSE", "BRK-B"),
    ("SMH", "NASDAQ", "SMH"),
    ("GOLD", None, "GOLD"),
])
def test_yahoo_symbol(symbol, exchange, expected):
    assert sync.yahoo_symbol(symbol, exchange) == expected


def test_overrides_win():
    assert sync.map_symbol("GOLD", None, {"GOLD": "GC=F"}) == "GC=F"


def test_trade_to_transaction():
    tx = sync.trade_to_transaction(activity(), "TFSA")
    assert (tx.symbol, tx.side, tx.quantity, tx.price, tx.trade_date) == ("HISU-U.TO", "buy", 200.0, 100.1352, "20260708")
    assert tx.comment == "Wealthsimple Market Buy TFSA"
    assert tx.external_id == "order-1"


def test_sell_dated_in_toronto_time():
    # 01:35 UTC on July 2 is the evening of July 1 in Toronto.
    tx = sync.trade_to_transaction(activity(type="DIY_SELL", subType="LIMIT_ORDER",
                                            occurredAt="2026-07-02T01:35:00+00:00"), "TFSA")
    assert (tx.side, tx.trade_date, tx.comment) == ("sell", "20260701", "Wealthsimple Limit Sell TFSA")


def test_journal_becomes_sell_and_buy():
    journal = activity(type="JOURNAL_SHARES", subType=None, canonicalId="journal-1", assetSymbol="DLR.U",
                       counterAssetSymbol="DLR", assetQuantity="1000", amount="14240.00", fees="9.95",
                       occurredAt="2026-09-23T13:30:05+00:00", security=security("DLR.U", "TSX"))
    prior_buy = activity(assetSymbol="DLR.U", assetQuantity="1000", amount="10160.00")
    out, into = sync.journal_to_transactions(journal, prior_buy, "TFSA")
    assert (out.symbol, out.side, out.price, out.external_id) == ("DLR-U.TO", "sell", 10.16, "journal-1:out")
    assert (into.symbol, into.side, into.price, into.commission) == ("DLR.TO", "buy", 14.24, 9.95)


def test_dates():
    assert trade_date("2026-09-25T14:53:13.139000+00:00") == "20260925"
    assert to_utc("2026-07-10", end_of_day=False) == "2026-07-10T04:00:00.000Z"
    assert to_utc("2026-07-10", end_of_day=True) == "2026-07-11T03:59:59.999Z"


def test_diff_reports_only_mismatches():
    ws_positions = [
        {"quantity": "120", "security": security("CASH", "TSX")},
        {"quantity": "40", "security": security("HISU.U", "TSX")},
        {"quantity": "2", "security": {"id": "sec-p-1", "stock": {"symbol": "GOLD", "primaryExchange": None}}},
    ]
    lot = Lot("lot_1", "", 1, 1, "20260101")
    portfolio = Portfolio("p_1", "Test", "USD", [
        Position("pos_1", "CASH.TO", 120.0, [lot]),
        Position("pos_2", "HISU-U.TO", 52.5, [lot]),
        Position("pos_3", "GC=F", 2.0, [lot]),
        Position("pos_4", "AAPL", 0.0, []),  # tracked only: ignored
    ])
    assert sync.diff(ws_positions, portfolio, {"GOLD": "GC=F"}) == [
        {"symbol": "HISU-U.TO", "wealthsimple": 40.0, "yahoo": 52.5, "diff": -12.5},
    ]


def test_ledger_resume_date(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    assert sync.resume_date("p_1") is None
    sync.mark_synced("p_1", "order-1", "20260708")
    sync.mark_synced("p_1", "order-2", "20260925")
    sync.mark_synced("p_2", "order-3", "20261001")
    assert set(sync.synced("p_1")) == {"order-1", "order-2"}
    assert sync.resume_date("p_1") == "2026-09-25"
