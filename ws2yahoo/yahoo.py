"""Unofficial Yahoo Finance portfolio client.

Talks to the private ``query1.finance.yahoo.com`` endpoints the Yahoo Finance
web app uses. The only credential is your Yahoo session cookie; the crumb (an
anti-CSRF token) and your user id are looked up from it.

Yahoo's firewall fingerprints TLS, so requests go through ``curl_cffi``
impersonating Chrome. Plain ``requests`` gets ``429 Too Many Requests``.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

from curl_cffi import requests

from . import config
from .browser import Browser, cookie_header
from .models import Lot, Portfolio, Position, Transaction

BASE = "https://query1.finance.yahoo.com"


class YahooError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class YahooClient:
    def __init__(self, cookie: str, lang: str = "en-CA", region: str = "CA"):
        self.lang, self.region = lang, region
        self.session = requests.Session(impersonate="chrome")
        self.session.headers.update({"Cookie": cookie, "Referer": "https://finance.yahoo.com/"})
        self.crumb = self._fetch_crumb()
        self._user_id: Optional[str] = None

    def _fetch_crumb(self) -> str:
        r = self.session.get(f"{BASE}/v1/test/getcrumb")
        crumb = r.text.strip()
        if r.status_code != 200 or not crumb or "<" in crumb or " " in crumb:
            raise YahooError("Yahoo session is invalid or expired. Log in at finance.yahoo.com and run "
                             "`ws2yahoo auth`.", r.status_code)
        return crumb

    def _params(self, **extra) -> dict:
        return {"lang": self.lang, "region": self.region, "crumb": self.crumb, **extra}

    @staticmethod
    def _check(resp) -> dict:
        if resp.status_code == 429:
            raise YahooError("Yahoo returned 429 Too Many Requests. Wait a few minutes and retry; if it "
                             "persists, run `ws2yahoo auth`.", 429)
        try:
            data = resp.json()
        except ValueError:
            raise YahooError(f"Non-JSON response (status {resp.status_code}): {resp.text[:120]!r}", resp.status_code)
        if isinstance(data, dict) and (data.get("finance") or {}).get("error"):
            err = data["finance"]["error"]
            raise YahooError(err.get("description", str(err)), resp.status_code)
        return data

    # -- read -----------------------------------------------------------
    def _raw_portfolios(self) -> list[dict]:
        data = self._check(self.session.get(f"{BASE}/v7/finance/desktop/portfolio",
                                            params=self._params(formatted="true")))
        portfolios = data["finance"]["result"][0].get("portfolios", [])
        if portfolios and not self._user_id:
            self._user_id = portfolios[0].get("userId")
        return portfolios

    @property
    def user_id(self) -> str:
        if not self._user_id:
            self._raw_portfolios()
        if not self._user_id:
            raise YahooError("Could not determine your Yahoo user id (no portfolios?)")
        return self._user_id

    def list_portfolios(self) -> list[Portfolio]:
        return [_portfolio(p, with_positions=False) for p in self._raw_portfolios()]

    def get_portfolio(self, pf_id: str) -> Portfolio:
        for p in self._raw_portfolios():
            if p["pfId"] == pf_id:
                return _portfolio(p)
        raise YahooError(f"Portfolio {pf_id!r} not found. Run `ws2yahoo portfolios` to list them.")

    # -- write ------------------------------------------------------------
    def add_transaction(self, pf_id: str, tx: Transaction) -> str:
        """Record a buy or sell (same API as the web UI). Returns the transaction id.

        The symbol must already be tracked in the portfolio (see :meth:`add_position`).
        """
        pos = self.get_portfolio(pf_id).find_position(tx.symbol)
        if not pos:
            raise YahooError(f"{tx.symbol!r} is not tracked in {pf_id!r}; add the position first.")
        body = {"transaction": {
            "pfId": pf_id,
            "positionId": pos.pos_id,
            "type": "SELL" if tx.side == "sell" else "BUY",
            "date": tx.trade_date,
            "quantity": abs(tx.quantity),
            "pricePerShare": tx.price,
            "commission": tx.commission,
            "comment": tx.comment,
        }}
        resp = self.session.post(f"{BASE}/ws/portfolio-api/v1/portfolio/transaction?{urlencode(self._params())}",
                                 json=body, headers={"x-crumb": self.crumb})
        if resp.status_code != 200:
            raise YahooError(f"Transaction failed (status {resp.status_code}): {resp.text[:120]!r}", resp.status_code)
        return resp.json().get("newTransactionMeta", {}).get("id", "")

    def _update(self, pf_id: str, operations: list[dict]) -> dict:
        params = self._params(action="update", formatted="true", pfId=pf_id, userId=self.user_id)
        body = {"operations": operations,
                "parameters": {"fullResponse": True, "pfId": pf_id, "userId": self.user_id, "userIdType": "guid"}}
        return self._check(self.session.put(f"{BASE}/v6/finance/portfolio/update", params=params, json=body))

    def add_position(self, pf_id: str, symbol: str) -> str:
        """Start tracking a ticker (with no lots). Returns the new posId."""
        # Unlike the other operations, position_insert reports an error when
        # "onePortfolio" is set, even though the position gets created.
        self._update(pf_id, [{"operation": "position_insert", "symbol": symbol, "sortOrder": 0}])
        pos = self.get_portfolio(pf_id).find_position(symbol)
        if not pos:
            raise YahooError(f"Yahoo did not create a position for {symbol!r} in {pf_id!r}")
        return pos.pos_id

    def delete_lot(self, pf_id: str, lot_id: str, symbol: str) -> dict:
        return self._update(pf_id, [{"operation": "lot_delete", "lotId": lot_id, "symbol": symbol,
                                     "onePortfolio": True}])

    def delete_position(self, pf_id: str, pos_id: str) -> dict:
        """Remove a ticker and all of its transactions."""
        return self._update(pf_id, [{"operation": "position_delete", "posId": pos_id, "onePortfolio": True}])


def _portfolio(p: dict, with_positions: bool = True) -> Portfolio:
    positions = []
    for pos in p.get("positions", []) if with_positions else []:
        lots = [Lot(lot_id=lot["lotId"], symbol=pos["symbol"], quantity=float(lot.get("quantity", 0)),
                    purchase_price=float(lot.get("purchasePrice", 0)), trade_date=lot.get("tradeDate", ""),
                    commission=float(lot.get("commission") or 0), comment=lot.get("comment") or "")
                for lot in pos.get("lots", [])]
        positions.append(Position(pos_id=pos["posId"], symbol=pos["symbol"],
                                  quantity=float(pos.get("quantity", 0)), lots=lots))
    name = p.get("pfName", "").replace("&#39;", "'").replace("&amp;", "&").replace("&quot;", '"')
    return Portfolio(pf_id=p["pfId"], name=name, base_currency=p.get("baseCurrency", ""), positions=positions)


# -- session ----------------------------------------------------------------
def _signed_in(cookie: str) -> YahooClient:
    client = YahooClient(cookie)
    client.user_id  # a signed-out cookie still gets a crumb, but can't read portfolios
    return client


def login_from_browser(browser: Optional[Browser] = None) -> YahooClient:
    """Read the Yahoo cookie from the browser, check it, and save it."""
    if browser is None:
        with Browser() as b:
            return login_from_browser(b)
    cookie = cookie_header(browser.cookies(), "query1.finance.yahoo.com")
    try:
        client = _signed_in(cookie)
    except YahooError as e:
        raise YahooError(f"No usable Yahoo login in the browser ({e}). Log in at finance.yahoo.com and retry.")
    config.save("yahoo.json", {"cookie": cookie}, private=True)
    return client


def get_client() -> YahooClient:
    """Client from the saved cookie, re-reading it from the browser if it has expired."""
    cookie = config.load("yahoo.json", {}).get("cookie")
    if cookie:
        try:
            return _signed_in(cookie)
        except YahooError:
            pass
    return login_from_browser()
