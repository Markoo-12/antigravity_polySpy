"""
Paper Trader - Simulates copy-trading every alert with realistic execution.

Applies configurable slippage and fees, tracks open positions,
auto-closes on market resolution, and reports portfolio performance.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import aiosqlite
import aiohttp

from ..config import DATABASE_PATH

# Polymarket CLOB API
CLOB_API_BASE = "https://clob.polymarket.com"

# Default configuration
DEFAULT_POSITION_SIZE = 2000.0    # $2,000 per trade
DEFAULT_SLIPPAGE_PCT = 0.015      # 1.5% average slippage
DEFAULT_FEE_PCT = 0.005           # 0.5% fee
DEFAULT_TIMEOUT_DAYS = 30         # Auto-close after 30 days
DEFAULT_CHECK_INTERVAL = 900      # 15 minutes


PAPER_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    asset_id TEXT NOT NULL,
    side TEXT NOT NULL,
    insider_score INTEGER,
    entry_price REAL NOT NULL,
    entry_price_with_slippage REAL NOT NULL,
    position_size_usdc REAL NOT NULL,
    shares REAL NOT NULL,
    simulated_slippage_pct REAL,
    simulated_fee_usdc REAL,
    opened_at DATETIME NOT NULL,
    -- Close fields
    exit_price REAL,
    closed_at DATETIME,
    close_reason TEXT,
    gross_pnl_usdc REAL,
    net_pnl_usdc REAL,
    roi_pct REAL,
    status TEXT DEFAULT 'open',
    market_slug TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_paper_trades_asset ON paper_trades(asset_id);
"""


@dataclass
class PaperTrade:
    """Represents a simulated paper trade."""
    id: Optional[int]
    signal_id: Optional[int]
    asset_id: str
    side: str
    insider_score: int
    entry_price: float
    entry_price_with_slippage: float
    position_size_usdc: float
    shares: float
    simulated_slippage_pct: float
    simulated_fee_usdc: float
    opened_at: datetime
    exit_price: Optional[float] = None
    closed_at: Optional[datetime] = None
    close_reason: Optional[str] = None
    gross_pnl_usdc: Optional[float] = None
    net_pnl_usdc: Optional[float] = None
    roi_pct: Optional[float] = None
    status: str = "open"
    market_slug: Optional[str] = None


class PaperTrader:
    """
    Simulates copy-trading every alert with realistic execution.

    Usage:
        trader = PaperTrader(db_path)
        await trader.init()
        await trader.open_position(asset_id, entry_price, "buy", 85)
        await trader.start_checker()  # background resolution checker
    """

    def __init__(
        self,
        db_path: str = DATABASE_PATH,
        position_size: float = DEFAULT_POSITION_SIZE,
        slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
        fee_pct: float = DEFAULT_FEE_PCT,
        timeout_days: int = DEFAULT_TIMEOUT_DAYS,
        check_interval: int = DEFAULT_CHECK_INTERVAL,
    ):
        self.db_path = db_path
        self.position_size = position_size
        self.slippage_pct = slippage_pct
        self.fee_pct = fee_pct
        self.timeout_days = timeout_days
        self.check_interval = check_interval
        self._checker_task: Optional[asyncio.Task] = None
        self._running = False

    async def init(self) -> None:
        """Initialize the paper_trades table."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(PAPER_TRADES_SCHEMA)
            await db.commit()
        print(f"[PAPER] Paper trader initialized (size=${self.position_size:,.0f}, slippage={self.slippage_pct*100:.1f}%, fee={self.fee_pct*100:.1f}%)")

    async def open_position(
        self,
        asset_id: str,
        entry_price: float,
        side: str,
        insider_score: int,
        signal_id: Optional[int] = None,
        market_slug: Optional[str] = None,
    ) -> Optional[int]:
        """
        Open a new paper trade position.

        Applies slippage to simulate realistic execution:
        - BUY: price goes UP by slippage (worse fill)
        - SELL: price goes DOWN by slippage (worse fill)

        Returns:
            Paper trade ID, or None if position invalid.
        """
        if entry_price <= 0 or entry_price >= 1:
            print(f"   [PAPER] Skipping — invalid entry price: {entry_price}")
            return None

        # Apply slippage
        if side == "buy":
            slippage_price = entry_price * (1 + self.slippage_pct)
            slippage_price = min(slippage_price, 0.99)  # Cap at 99 cents
        else:
            slippage_price = entry_price * (1 - self.slippage_pct)
            slippage_price = max(slippage_price, 0.01)  # Floor at 1 cent

        # Calculate shares and fee
        fee = self.position_size * self.fee_pct
        effective_size = self.position_size - fee
        shares = effective_size / slippage_price

        now = datetime.now(timezone.utc)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO paper_trades (
                    signal_id, asset_id, side, insider_score,
                    entry_price, entry_price_with_slippage,
                    position_size_usdc, shares,
                    simulated_slippage_pct, simulated_fee_usdc,
                    opened_at, market_slug
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id, asset_id, side, insider_score,
                    entry_price, round(slippage_price, 6),
                    self.position_size, round(shares, 4),
                    self.slippage_pct, round(fee, 2),
                    now.isoformat(), market_slug,
                ),
            )
            await db.commit()
            trade_id = cursor.lastrowid

        print(
            f"   [PAPER] Opened #{trade_id}: {side.upper()} {shares:.1f} shares "
            f"@ ${slippage_price:.4f} (slip={self.slippage_pct*100:.1f}%, fee=${fee:.2f})"
        )
        return trade_id

    async def close_position(
        self,
        paper_id: int,
        exit_price: float,
        reason: str = "resolved",
    ) -> Optional[float]:
        """
        Close a paper trade and calculate PnL.

        Returns:
            Net PnL in USDC, or None on error.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM paper_trades WHERE id = ? AND status = 'open'",
                (paper_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None

            side = row["side"]
            entry_with_slip = row["entry_price_with_slippage"]
            shares = row["shares"]
            position_size = row["position_size_usdc"]
            fee = row["simulated_fee_usdc"]

            # Calculate PnL
            if side == "buy":
                # Bought shares at entry_with_slip, sell at exit_price
                gross_pnl = (exit_price - entry_with_slip) * shares
            else:
                # Sold (shorted) at entry_with_slip, cover at exit_price
                gross_pnl = (entry_with_slip - exit_price) * shares

            net_pnl = gross_pnl - fee  # Fee already deducted on entry, but for symmetry
            roi = net_pnl / position_size * 100

            now = datetime.now(timezone.utc)

            await db.execute(
                """
                UPDATE paper_trades
                SET exit_price = ?, closed_at = ?, close_reason = ?,
                    gross_pnl_usdc = ?, net_pnl_usdc = ?, roi_pct = ?,
                    status = 'closed'
                WHERE id = ?
                """,
                (
                    exit_price, now.isoformat(), reason,
                    round(gross_pnl, 2), round(net_pnl, 2), round(roi, 2),
                    paper_id,
                ),
            )
            await db.commit()

        result = "[WIN]" if net_pnl > 0 else "[LOSS]"
        print(
            f"[PAPER] {result} Closed #{paper_id}: {reason} | "
            f"PnL=${net_pnl:+.2f} ({roi:+.1f}%)"
        )
        return net_pnl

    async def start_checker(self) -> None:
        """Start background task to check for resolved markets and timeouts."""
        self._running = True
        self._checker_task = asyncio.create_task(self._check_loop())
        print(f"[PAPER] Background resolution checker started")

    async def stop_checker(self) -> None:
        """Stop the background checker."""
        self._running = False
        if self._checker_task:
            self._checker_task.cancel()
            try:
                await self._checker_task
            except asyncio.CancelledError:
                pass
        print("[PAPER] Background resolution checker stopped")

    async def _check_loop(self) -> None:
        """Periodically check open positions for resolution or timeout."""
        while self._running:
            try:
                await self._check_open_positions()
            except Exception as e:
                print(f"[PAPER] Error in check loop: {e}")
            await asyncio.sleep(self.check_interval)

    async def _check_open_positions(self) -> None:
        """Check all open positions for resolution or timeout."""
        open_trades = await self._get_open_trades()
        if not open_trades:
            return

        now = datetime.now(timezone.utc)
        closed = 0

        for trade in open_trades:
            try:
                # Check timeout first
                opened_at = trade.opened_at
                if opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=timezone.utc)

                if (now - opened_at).days >= self.timeout_days:
                    # Fetch current price for timeout close
                    current = await self._fetch_current_price(trade.asset_id)
                    exit_price = current if current else trade.entry_price
                    await self.close_position(trade.id, exit_price, "timeout")
                    closed += 1
                    continue

                # Check for resolution
                current = await self._fetch_current_price(trade.asset_id)
                if current is not None:
                    if current >= 0.95:
                        await self.close_position(trade.id, 1.0, "resolved_yes")
                        closed += 1
                    elif current <= 0.05:
                        await self.close_position(trade.id, 0.0, "resolved_no")
                        closed += 1

            except Exception as e:
                print(f"[PAPER] Error checking trade #{trade.id}: {e}")

        if closed > 0:
            print(f"[PAPER] Closed {closed}/{len(open_trades)} positions")

    async def _get_open_trades(self) -> List[PaperTrade]:
        """Get all open paper trades."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY opened_at"
            )
            rows = await cursor.fetchall()
            return [self._row_to_trade(row) for row in rows]

    async def _fetch_current_price(self, asset_id: str) -> Optional[float]:
        """Fetch current price from CLOB API."""
        try:
            url = f"{CLOB_API_BASE}/prices-history"
            params = {"market": asset_id, "interval": "1h", "fidelity": 1}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    history = data.get("history", [])
                    if history:
                        return float(history[-1].get("p", 0))
            return None
        except Exception:
            return None

    async def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get comprehensive portfolio performance summary."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Open positions
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt, SUM(position_size_usdc) as total_invested FROM paper_trades WHERE status = 'open'"
            )
            open_row = await cursor.fetchone()

            # Closed positions
            cursor = await db.execute(
                """
                SELECT
                    COUNT(*) as total_closed,
                    SUM(CASE WHEN net_pnl_usdc > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN net_pnl_usdc <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(net_pnl_usdc) as total_pnl,
                    AVG(roi_pct) as avg_roi,
                    AVG(simulated_fee_usdc) as avg_fee,
                    AVG(simulated_slippage_pct) as avg_slippage,
                    MIN(net_pnl_usdc) as worst_trade,
                    MAX(net_pnl_usdc) as best_trade,
                    SUM(position_size_usdc) as total_capital_deployed
                FROM paper_trades
                WHERE status = 'closed'
                """
            )
            closed_row = await cursor.fetchone()

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
                        SUM(CASE WHEN net_pnl_usdc > 0 THEN 1 ELSE 0 END) as wins,
                        AVG(roi_pct) as avg_roi,
                        SUM(net_pnl_usdc) as total_pnl
                    FROM paper_trades
                    WHERE status = 'closed'
                      AND insider_score >= ? AND insider_score <= ?
                    """,
                    (tier_min, tier_max),
                )
                tier_row = await cursor.fetchone()
                t = tier_row["total"] or 0
                w = tier_row["wins"] or 0
                tiers[label] = {
                    "total": t,
                    "wins": w,
                    "win_rate": round(w / t * 100, 1) if t > 0 else 0,
                    "avg_roi": round(tier_row["avg_roi"] or 0, 2),
                    "total_pnl": round(tier_row["total_pnl"] or 0, 2),
                }

            # By close reason
            cursor = await db.execute(
                """
                SELECT close_reason, COUNT(*) as cnt, SUM(net_pnl_usdc) as pnl
                FROM paper_trades WHERE status = 'closed'
                GROUP BY close_reason
                """
            )
            reason_rows = await cursor.fetchall()
            by_reason = {row["close_reason"]: {"count": row["cnt"], "pnl": round(row["pnl"] or 0, 2)} for row in reason_rows}

        total_closed = closed_row["total_closed"] or 0
        wins = closed_row["wins"] or 0

        return {
            "open_positions": open_row["cnt"] or 0,
            "open_capital": round(open_row["total_invested"] or 0, 2),
            "total_closed": total_closed,
            "wins": wins,
            "losses": closed_row["losses"] or 0,
            "win_rate": round(wins / total_closed * 100, 1) if total_closed > 0 else 0,
            "total_pnl": round(closed_row["total_pnl"] or 0, 2),
            "avg_roi": round(closed_row["avg_roi"] or 0, 2),
            "best_trade": round(closed_row["best_trade"] or 0, 2),
            "worst_trade": round(closed_row["worst_trade"] or 0, 2),
            "total_capital_deployed": round(closed_row["total_capital_deployed"] or 0, 2),
            "avg_fee": round(closed_row["avg_fee"] or 0, 2),
            "avg_slippage": round((closed_row["avg_slippage"] or 0) * 100, 2),
            "by_score_tier": tiers,
            "by_close_reason": by_reason,
        }

    async def get_report(self) -> str:
        """Generate a human-readable paper trading report."""
        summary = await self.get_portfolio_summary()

        lines = [
            "",
            "═" * 56,
            "  PAPER TRADING — PORTFOLIO REPORT",
            "═" * 56,
            f"  Position Size:    ${self.position_size:,.0f} per trade",
            f"  Slippage Model:   {self.slippage_pct*100:.1f}%",
            f"  Fee Model:        {self.fee_pct*100:.1f}%",
            "─" * 56,
            f"  Open Positions:   {summary['open_positions']}  (${summary['open_capital']:,.0f} deployed)",
            f"  Closed Trades:    {summary['total_closed']}",
        ]

        if summary["total_closed"] > 0:
            lines.extend([
                "─" * 56,
                "  PERFORMANCE",
                f"  Win Rate:         {summary['win_rate']:.1f}% ({summary['wins']}/{summary['total_closed']})",
                f"  Total PnL:        ${summary['total_pnl']:+,.2f}",
                f"  Avg ROI:          {summary['avg_roi']:+.2f}%",
                f"  Best Trade:       ${summary['best_trade']:+,.2f}",
                f"  Worst Trade:      ${summary['worst_trade']:+,.2f}",
                f"  Capital Deployed: ${summary['total_capital_deployed']:,.0f}",
                f"  Avg Fee:          ${summary['avg_fee']:.2f}",
                f"  Avg Slippage:     {summary['avg_slippage']:.2f}%",
                "─" * 56,
                "  BY SCORE TIER",
            ])
            for tier, data in summary["by_score_tier"].items():
                if data["total"] > 0:
                    lines.append(
                        f"  {tier}:  Win {data['win_rate']:.0f}% | "
                        f"ROI {data['avg_roi']:+.1f}% | "
                        f"PnL ${data['total_pnl']:+,.0f} | n={data['total']}"
                    )

            if summary["by_close_reason"]:
                lines.append("─" * 56)
                lines.append("  BY CLOSE REASON")
                for reason, data in summary["by_close_reason"].items():
                    lines.append(f"  {reason}: {data['count']} trades | PnL ${data['pnl']:+,.2f}")

        else:
            lines.append("  No trades closed yet. Waiting for market outcomes...")

        lines.append("═" * 56)
        return "\n".join(lines)

    def _row_to_trade(self, row: aiosqlite.Row) -> PaperTrade:
        """Convert a database row to a PaperTrade object."""
        opened_at = row["opened_at"]
        if isinstance(opened_at, str):
            opened_at = datetime.fromisoformat(opened_at)

        closed_at = row["closed_at"]
        if isinstance(closed_at, str) and closed_at:
            closed_at = datetime.fromisoformat(closed_at)

        return PaperTrade(
            id=row["id"],
            signal_id=row["signal_id"],
            asset_id=row["asset_id"],
            side=row["side"],
            insider_score=row["insider_score"],
            entry_price=row["entry_price"],
            entry_price_with_slippage=row["entry_price_with_slippage"],
            position_size_usdc=row["position_size_usdc"],
            shares=row["shares"],
            simulated_slippage_pct=row["simulated_slippage_pct"],
            simulated_fee_usdc=row["simulated_fee_usdc"],
            opened_at=opened_at,
            exit_price=row["exit_price"],
            closed_at=closed_at,
            close_reason=row["close_reason"],
            gross_pnl_usdc=row["gross_pnl_usdc"],
            net_pnl_usdc=row["net_pnl_usdc"],
            roi_pct=row["roi_pct"],
            status=row["status"],
            market_slug=row["market_slug"],
        )
