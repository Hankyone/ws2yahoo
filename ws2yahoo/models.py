"""Plain dataclasses shared by the Yahoo and Wealthsimple sides."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Lot:
    lot_id: str
    symbol: str
    quantity: float
    purchase_price: float
    trade_date: str  # YYYYMMDD
    commission: float = 0.0
    comment: str = ""


@dataclass
class Position:
    pos_id: str
    symbol: str
    quantity: float
    lots: list[Lot] = field(default_factory=list)

    @property
    def has_holdings(self) -> bool:
        return bool(self.lots)


@dataclass
class Portfolio:
    pf_id: str
    name: str
    base_currency: str
    positions: list[Position] = field(default_factory=list)

    def find_position(self, symbol: str) -> Optional[Position]:
        return next((p for p in self.positions if p.symbol == symbol), None)


@dataclass
class Transaction:
    """One buy or sell to record in a Yahoo portfolio. ``quantity`` is always positive."""

    symbol: str
    quantity: float
    price: float
    trade_date: str  # YYYYMMDD
    side: str = "buy"  # "buy" or "sell"
    commission: float = 0.0
    comment: str = ""
    account: str = ""  # source account label, e.g. "TFSA" (not sent to Yahoo)
    external_id: str = ""  # Wealthsimple activity id (not sent to Yahoo)
