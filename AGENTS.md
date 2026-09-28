# AGENTS.md — ws2yahoo

Guide for agents working on this codebase. To *use* the tool, follow
[skills/ws2yahoo/SKILL.md](skills/ws2yahoo/SKILL.md). If `AGENTS.local.md`
exists, read it too: it holds the owner's personal setup and is never committed.

## Layout

- `ws2yahoo/browser.py` — minimal Chrome DevTools client; reads cookies and opens background tabs.
- `ws2yahoo/yahoo.py` — Yahoo Finance portfolio client (`YahooClient`) and its login.
- `ws2yahoo/wealthsimple.py` — read-only Wealthsimple GraphQL client and its login.
- `ws2yahoo/sync.py` — symbol mapping, activity → transactions, the sync ledger, `apply`, `diff`.
- `ws2yahoo/config.py` — local files in `~/.config/ws2yahoo` (`WS2YAHOO_HOME` overrides).
- `ws2yahoo/cli.py` — argparse CLI; every command supports `--json`.
- `tests/` — offline pytest suite: `pip install -e ".[dev]" && pytest`.

## Logins

Both logins come from the user's Chrome over DevTools. Chrome publishes the
endpoint in `<profile dir>/DevToolsActivePort` once remote debugging is on at
`chrome://inspect/#remote-debugging`, and it asks the user to **Allow** each
new connection, so keep connections rare: saved sessions are reused until they
expire, and `auth` reads both sites over one connection.

- Yahoo: the cookie header the browser sends to `query1.finance.yahoo.com`.
  The crumb comes from `GET /v1/test/getcrumb`; the user id from the portfolio
  list. A signed-out cookie still gets a crumb, so validity is checked by
  reading portfolios.
- Wealthsimple: the web app keeps its OAuth token in the `_oauth2_access_v2`
  cookie (URL-encoded JSON: `access_token`, `identity_canonical_id`,
  `created_at`, `expires_in` ≈ 1800 s). When it's stale, loading
  `my.wealthsimple.com` in a background tab makes the app renew it. **Never use
  the refresh token**: rotating it signs the browser out.

## Wealthsimple API (read-only)

`POST https://my.wealthsimple.com/graphql` with `Authorization: Bearer <token>`,
`x-ws-profile: trade`, `x-ws-api-version: 12`, `x-ws-locale`, `x-platform-os: web`.

- `FetchActivityFeedItems` — `activityFeedItems(first, after, condition, orderBy)`.
  `condition` accepts `startDate`, `endDate`, `types`, `accountIds`,
  `securityIds`, `unifiedStatuses`. Trades are `DIY_BUY`/`DIY_SELL`
  (`subType` MARKET_ORDER, LIMIT_ORDER, STOP_LIMIT_ORDER, FRACTIONAL_ORDER,
  DIVIDEND_REINVESTMENT); `amount` is the total value, so price = amount / assetQuantity.
  `JOURNAL_SHARES` moves shares between listings (`assetSymbol` → `counterAssetSymbol`)
  and is valued only in the receiving listing's currency.
- `FetchAccounts` — `identity(id).accounts` (id, type, nickname, status).
- `FetchIdentityPositions` — `identity(id).financials(filter).current(currency).positions(aggregated: true)`.

To discover more, record the web app's requests (they're named in the
`x-ws-operation-name` header) and trim the query to the fields you need.

## Yahoo API

- `GET /v7/finance/desktop/portfolio` — portfolios with positions and lots.
- `POST /ws/portfolio-api/v1/portfolio/transaction` (+ `x-crumb` header) — record a
  buy or sell: `{transaction: {pfId, positionId, type: "BUY"|"SELL", date,
  quantity (positive), pricePerShare, commission, comment}}`. Used for both
  sides; a lot with negative quantity shows up as a negative buy, not a sell.
- `PUT /v6/finance/portfolio/update` — `position_insert` (send it **without**
  `onePortfolio`, which makes it report an error even though it succeeds),
  `lot_delete`, `position_delete`.
- Requests need `curl_cffi` impersonating Chrome; plain `requests` gets 429 from
  Yahoo's firewall.

## Conventions

- Keep Wealthsimple access read-only.
- When probing against a real portfolio, clean up every test lot and position.
- Keep personal data (portfolio ids, trades, account ids) out of the repo.
