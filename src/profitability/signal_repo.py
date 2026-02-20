"""
Signal Repository - Manages signal tracking for profitability analysis.
Records every alert fired and tracks price outcomes over time.
"""
import aiosqlite
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER,
    asset_id TEXT NOT NULL,
    side TEXT NOT NULL,
    insider_score INTEGER,
    entry_price REAL,
    alert_timestamp DATETIME,
    -- Outcome tracking (filled later by background checker)
    price_1h REAL,
    price_6h REAL,
    price_24h REAL,
    price_48h REAL,
    resolved_price REAL,
    pnl_1h REAL,
    pnl_24h REAL,
    pnl_resolved REAL,
    outcome TEXT DEFAULT 'pending',
    checked_at DATETIME,
    market_slug TEXT,
    owner_address TEXT,
    trade_amount_usdc REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome);
CREATE INDEX IF NOT EXISTS idx_signals_asset ON signals(asset_id);
CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(alert_timestamp);
"""


@dataclass
class Signal:
    """A recorded alert signal with outcome tracking."""
    id: Optional[int]
    trade_id: Optional[int]
    asset_id: str
    side: str
    insider_score: int
    entry_price: float
    alert_timestamp: datetime
    price_1h: Optional[float] = None
    price_6h: Optional[float] = None
    price_24h: Optional[float] = None
    price_48h: Optional[float] = None
    resolved_price: Optional[float] = None
    pnl_1h: Optional[float] = None
    pnl_24h: Optional[float] = None
    pnl_resolved: Optional[float] = None
    outcome: str = "pending"
    checked_at: Optional[datetime] = None
    market_slug: Optional[str] = None
    owner_address: Optional[str] = None
    trade_amount_usdc: Optional[float] = None


class SignalRepository:
    """Async repository for signal tracking operations."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_table(self) -> None:
        """Create the signals table if it doesn't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SIGNALS_SCHEMA)
            await db.commit()

    async def insert_signal(
        self,
        trade_id: Optional[int],
        asset_id: str,
        side: str,
        insider_score: int,
        entry_price: float,
        alert_timestamp: datetime,
        market_slug: Optional[str] = None,
        owner_address: Optional[str] = None,
        trade_amount_usdc: Optional[float] = None,
    ) -> int:
        """
        Record a new signal (alert fired).
        Returns the signal ID.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO signals (
                    trade_id, asset_id, side, insider_score, entry_price,
                    alert_timestamp, market_slug, owner_address, trade_amount_usdc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id, asset_id, side, insider_score, entry_price,
                    alert_timestamp.isoformat(), market_slug, owner_address,
                    trade_amount_usdc,
                ),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_pending_signals(self) -> List[Signal]:
        """Get all signals that haven't been fully resolved yet."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM signals
                WHERE outcome = 'pending'
                ORDER BY alert_timestamp ASC
                """
            )
            rows = await cursor.fetchall()
            return [self._row_to_signal(row) for row in rows]

    async def update_price_check(
        self,
        signal_id: int,
        price_1h: Optional[float] = None,
        price_6h: Optional[float] = None,
        price_24h: Optional[float] = None,
        price_48h: Optional[float] = None,
        resolved_price: Optional[float] = None,
    ) -> None:
        """Update price checkpoints for a signal."""
        async with aiosqlite.connect(self.db_path) as db:
            # Build dynamic update
            updates = []
            params = []

            if price_1h is not None:
                updates.append("price_1h = ?")
                params.append(price_1h)
            if price_6h is not None:
                updates.append("price_6h = ?")
                params.append(price_6h)
            if price_24h is not None:
                updates.append("price_24h = ?")
                params.append(price_24h)
            if price_48h is not None:
                updates.append("price_48h = ?")
                params.append(price_48h)
            if resolved_price is not None:
                updates.append("resolved_price = ?")
                params.append(resolved_price)

            if not updates:
                return

            updates.append("checked_at = ?")
            params.append(datetime.now(timezone.utc).isoformat())
            params.append(signal_id)

            query = f"UPDATE signals SET {', '.join(updates)} WHERE id = ?"
            await db.execute(query, params)
            await db.commit()

    async def update_pnl(
        self,
        signal_id: int,
        entry_price: float,
        side: str,
        price_1h: Optional[float] = None,
        price_24h: Optional[float] = None,
        resolved_price: Optional[float] = None,
    ) -> None:
        """Calculate and store PnL values for a signal."""
        updates = []
        params = []

        def calc_pnl(exit_p: float) -> float:
            if side == "buy":
                return (exit_p - entry_price) / entry_price * 100 if entry_price > 0 else 0
            else:
                return (entry_price - exit_p) / entry_price * 100 if entry_price > 0 else 0

        if price_1h is not None:
            pnl = calc_pnl(price_1h)
            updates.append("pnl_1h = ?")
            params.append(round(pnl, 2))

        if price_24h is not None:
            pnl = calc_pnl(price_24h)
            updates.append("pnl_24h = ?")
            params.append(round(pnl, 2))

        if resolved_price is not None:
            pnl = calc_pnl(resolved_price)
            updates.append("pnl_resolved = ?")
            params.append(round(pnl, 2))
            # Determine outcome
            outcome = "win" if pnl > 0 else "loss"
            updates.append("outcome = ?")
            params.append(outcome)

        if not updates:
            return

        params.append(signal_id)
        async with aiosqlite.connect(self.db_path) as db:
            query = f"UPDATE signals SET {', '.join(updates)} WHERE id = ?"
            await db.execute(query, params)
            await db.commit()

    async def get_stats(self, min_score: int = 0) -> Dict[str, Any]:
        """
        Get aggregate profitability statistics.

        Returns dict with win_rate, avg_roi, total_pnl, etc.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Total signals
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM signals WHERE insider_score >= ?",
                (min_score,),
            )
            total = (await cursor.fetchone())["cnt"]

            # Resolved signals
            cursor = await db.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) as losses,
                    AVG(pnl_resolved) as avg_pnl_resolved,
                    AVG(pnl_24h) as avg_pnl_24h,
                    AVG(pnl_1h) as avg_pnl_1h,
                    MIN(pnl_resolved) as worst_trade,
                    MAX(pnl_resolved) as best_trade
                FROM signals
                WHERE outcome != 'pending' AND insider_score >= ?
                """,
                (min_score,),
            )
            row = await cursor.fetchone()

            resolved = row["total"] or 0
            wins = row["wins"] or 0
            losses = row["losses"] or 0

            # By score tier
            tiers = {}
            for tier_min, tier_max, label in [
                (75, 84, "75-84"),
                (85, 94, "85-94"),
                (95, 200, "95+"),
            ]:
                cursor = await db.execute(
                    """
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
                        AVG(pnl_resolved) as avg_pnl
                    FROM signals
                    WHERE outcome != 'pending'
                      AND insider_score >= ? AND insider_score <= ?
                    """,
                    (tier_min, tier_max),
                )
                tier_row = await cursor.fetchone()
                tiers[label] = {
                    "total": tier_row["total"] or 0,
                    "wins": tier_row["wins"] or 0,
                    "win_rate": (
                        (tier_row["wins"] or 0) / tier_row["total"] * 100
                        if tier_row["total"]
                        else 0
                    ),
                    "avg_pnl": round(tier_row["avg_pnl"] or 0, 2),
                }

        return {
            "total_signals": total,
            "resolved": resolved,
            "pending": total - resolved,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / resolved * 100, 1) if resolved > 0 else 0,
            "avg_pnl_1h": round(row["avg_pnl_1h"] or 0, 2),
            "avg_pnl_24h": round(row["avg_pnl_24h"] or 0, 2),
            "avg_pnl_resolved": round(row["avg_pnl_resolved"] or 0, 2),
            "best_trade": round(row["best_trade"] or 0, 2),
            "worst_trade": round(row["worst_trade"] or 0, 2),
            "by_score_tier": tiers,
        }

    def _row_to_signal(self, row: aiosqlite.Row) -> Signal:
        """Convert a database row to a Signal object."""
        return Signal(
            id=row["id"],
            trade_id=row["trade_id"],
            asset_id=row["asset_id"],
            side=row["side"],
            insider_score=row["insider_score"],
            entry_price=row["entry_price"],
            alert_timestamp=datetime.fromisoformat(row["alert_timestamp"])
            if row["alert_timestamp"]
            else datetime.now(timezone.utc),
            price_1h=row["price_1h"],
            price_6h=row["price_6h"],
            price_24h=row["price_24h"],
            price_48h=row["price_48h"],
            resolved_price=row["resolved_price"],
            pnl_1h=row["pnl_1h"],
            pnl_24h=row["pnl_24h"],
            pnl_resolved=row["pnl_resolved"],
            outcome=row["outcome"],
            checked_at=datetime.fromisoformat(row["checked_at"])
            if row["checked_at"]
            else None,
            market_slug=row["market_slug"],
            owner_address=row["owner_address"],
            trade_amount_usdc=row["trade_amount_usdc"],
        )
