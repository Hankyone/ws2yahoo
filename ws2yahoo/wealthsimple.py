"""Read-only Wealthsimple client.

Wealthsimple's web app (``my.wealthsimple.com``) reads everything through a
GraphQL endpoint authorized with an OAuth bearer token, which it keeps in the
``_oauth2_access_v2`` cookie. We borrow that token from your browser.

Tokens live about 30 minutes. When ours expires we read the cookie again; if
the browser's copy is stale too, we open the web app in a background tab so it
renews the token itself. We never use the refresh token directly, because
rotating it would sign your browser out.

Only read queries are used. Nothing here can move money or place orders.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from datetime import datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from curl_cffi import requests

from . import config
from .browser import Browser

GRAPHQL = "https://my.wealthsimple.com/graphql"
APP_URL = "https://my.wealthsimple.com/app/home"
TZ = ZoneInfo("America/Toronto")  # Wealthsimple dates trades in Toronto time

_ACCOUNT_LABEL = {
    "tfsa": "TFSA",
    "rrsp": "RRSP",
    "fhsa": "FHSA",
    "resp": "RESP",
    "non_registered": "Non-registered",
    "non_registered_crypto": "Crypto",
    "ca_cash_msb": "Cash",
    "ca_cash": "Cash",
    "ca_credit_card": "Credit card",
}

_SECURITY = "security { id securityType currency stock { symbol primaryExchange } }"

_ACTIVITY_QUERY = """
query FetchActivityFeedItems($first: Int, $cursor: Cursor, $condition: ActivityCondition, $orderBy: [ActivitiesOrderBy!]) {
  activityFeedItems(first: $first, after: $cursor, condition: $condition, orderBy: $orderBy) {
    edges { node {
      canonicalId accountId type subType status unifiedStatus occurredAt
      assetSymbol assetQuantity counterAssetSymbol amount amountSign currency fees
      fxRate realizedPnl securityId %s
    } }
    pageInfo { hasNextPage endCursor }
  }
}""" % _SECURITY

_ACCOUNTS_QUERY = """
query FetchAccounts($identityId: ID!) {
  identity(id: $identityId) {
    accounts(first: 100) { edges { node { id type unifiedAccountType nickname status currency } } }
  }
}"""

_POSITIONS_QUERY = """
query FetchIdentityPositions($identityId: ID!, $currency: Currency!, $first: Int, $cursor: String, $accountIds: [ID!]) {
  identity(id: $identityId) {
    financials(filter: {accounts: $accountIds}) {
      current(currency: $currency) {
        positions(first: $first, after: $cursor, aggregated: true) {
          edges { node { quantity accounts { id } %s } }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}""" % _SECURITY


class WealthsimpleError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class WealthsimpleClient:
    def __init__(self, access_token: str, identity_id: str):
        self.identity_id = identity_id
        self.session = requests.Session(impersonate="chrome")
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "x-ws-profile": "trade",
            "x-ws-api-version": "12",
            "x-ws-locale": "en-CA",
            "x-platform-os": "web",
        })
        self._accounts: Optional[list[dict]] = None

    def _gql(self, operation: str, query: str, variables: dict) -> dict:
        resp = self.session.post(GRAPHQL, json={"operationName": operation, "query": query, "variables": variables})
        if resp.status_code == 401:
            raise WealthsimpleError("Wealthsimple token rejected. Run `ws2yahoo auth` to refresh it.", 401)
        try:
            data = resp.json()
        except ValueError:
            raise WealthsimpleError(f"Non-JSON response (status {resp.status_code}): {resp.text[:120]!r}",
                                    resp.status_code)
        if data.get("errors"):
            raise WealthsimpleError("; ".join(e.get("message", str(e)) for e in data["errors"]), resp.status_code)
        return data["data"]

    def _paginate(self, operation: str, query: str, variables: dict, path: list[str],
                  limit: Optional[int] = None):
        cursor, count = None, 0
        while True:
            data = self._gql(operation, query, {**variables, "cursor": cursor})
            for key in path:
                data = data[key]
            for edge in data["edges"]:
                yield edge["node"]
                count += 1
                if limit and count >= limit:
                    return
            if not data["pageInfo"]["hasNextPage"]:
                return
            cursor = data["pageInfo"]["endCursor"]

    def accounts(self) -> list[dict]:
        """All accounts (open and closed), each with an added ``label`` like "TFSA"."""
        if self._accounts is None:
            data = self._gql("FetchAccounts", _ACCOUNTS_QUERY, {"identityId": self.identity_id})
            self._accounts = []
            for edge in data["identity"]["accounts"]["edges"]:
                acct = edge["node"]
                acct["label"] = acct.get("nickname") or _ACCOUNT_LABEL.get(acct["type"], acct["type"])
                self._accounts.append(acct)
        return self._accounts

    def resolve_accounts(self, selectors: Optional[Iterable[str]]) -> Optional[list[str]]:
        """Turn account ids or types (``tfsa``, ``fhsa``, ``non_registered``...) into ids."""
        wanted = {s.strip().lower().replace("-", "_") for s in selectors or [] if s.strip()}
        if not wanted:
            return None
        ids = [a["id"] for a in self.accounts()
               if a["id"].lower().replace("-", "_") in wanted or a["type"] in wanted]
        if not ids:
            raise WealthsimpleError(f"No Wealthsimple accounts match {sorted(wanted)}")
        return ids

    def activities(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        types: Optional[list[str]] = None,
        account_ids: Optional[list[str]] = None,
        security_ids: Optional[list[str]] = None,
        statuses: Optional[list[str]] = None,
        oldest_first: bool = False,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Raw activity feed items, newest first unless ``oldest_first``.

        ``since``/``until`` are ``YYYY-MM-DD`` dates (Toronto time, inclusive)
        or ISO timestamps. Other filters take Wealthsimple values, e.g.
        ``types=["DIY_BUY", "DIVIDEND"]``, ``statuses=["COMPLETED"]``.
        """
        condition = {}
        if since:
            condition["startDate"] = to_utc(since, end_of_day=False)
        if until:
            condition["endDate"] = to_utc(until, end_of_day=True)
        if types:
            condition["types"] = types
        if account_ids:
            condition["accountIds"] = account_ids
        if security_ids:
            condition["securityIds"] = security_ids
        if statuses:
            condition["unifiedStatuses"] = statuses
        variables = {"first": min(limit or 100, 100), "condition": condition,
                     "orderBy": "OCCURRED_AT_ASC" if oldest_first else "OCCURRED_AT_DESC"}
        return list(self._paginate("FetchActivityFeedItems", _ACTIVITY_QUERY, variables,
                                   ["activityFeedItems"], limit))

    def positions(self, account_ids: Optional[list[str]] = None) -> list[dict]:
        """Current holdings, summed across the given accounts (default: all)."""
        variables = {"identityId": self.identity_id, "currency": "CAD", "first": 200, "accountIds": account_ids}
        return list(self._paginate("FetchIdentityPositions", _POSITIONS_QUERY, variables,
                                   ["identity", "financials", "current", "positions"]))


def trade_date(occurred_at: str) -> str:
    """ISO timestamp -> ``YYYYMMDD`` in Toronto time."""
    return datetime.fromisoformat(occurred_at).astimezone(TZ).strftime("%Y%m%d")


def to_utc(value: str, end_of_day: bool) -> str:
    """``YYYY-MM-DD`` (Toronto) -> UTC ISO timestamp; full timestamps pass through."""
    if "T" in value:
        return value
    day = datetime.strptime(value.replace("-", ""), "%Y%m%d")
    local = day.replace(hour=23, minute=59, second=59, microsecond=999000) if end_of_day else day
    return local.replace(tzinfo=TZ).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# -- session ----------------------------------------------------------------
def _read_token(browser: Browser) -> Optional[dict]:
    c = browser.cookie("_oauth2_access_v2", "wealthsimple.com")
    if not c:
        return None
    auth = json.loads(urllib.parse.unquote(c["value"]))
    expires_at = auth.get("created_at", 0) + auth.get("expires_in", 0)
    if not auth.get("access_token") or expires_at - time.time() < 300:
        return None
    return {"access_token": auth["access_token"], "identity_id": auth["identity_canonical_id"],
            "expires_at": expires_at}


def login_from_browser(browser: Optional[Browser] = None) -> WealthsimpleClient:
    """Borrow the Wealthsimple web app's token from the browser and save it."""
    if browser is None:
        with Browser() as b:
            return login_from_browser(b)
    token = _read_token(browser)
    if not token:
        # Loading the web app makes it renew the token and rewrite the cookie.
        tab = browser.open_background_tab(APP_URL)
        try:
            for _ in range(60):
                time.sleep(0.5)
                token = _read_token(browser)
                if token:
                    break
        finally:
            browser.close_tab(tab)
    if not token:
        raise WealthsimpleError("No Wealthsimple login in the browser. Log in at my.wealthsimple.com and retry.")
    config.save("wealthsimple.json", token, private=True)
    return WealthsimpleClient(token["access_token"], token["identity_id"])


def get_client() -> WealthsimpleClient:
    """Client from the saved token, re-reading it from the browser when expired."""
    token = config.load("wealthsimple.json", {})
    if token.get("expires_at", 0) - time.time() > 60:
        client = WealthsimpleClient(token["access_token"], token["identity_id"])
        try:
            client.accounts()  # cached for later use; confirms the token wasn't revoked early
            return client
        except WealthsimpleError as e:
            if e.status != 401:
                raise
    return login_from_browser()
