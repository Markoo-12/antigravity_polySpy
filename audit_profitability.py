"""
Retroactive Profitability Audit
Analyzes historical trades in the database to determine if the Sentinel's
alerts would have been profitable.

Usage:
    py audit_profitability.py
    py audit_profitability.py --threshold 85
    py audit_profitability.py --position-size 5000
    py audit_profitability.py --threshold 75 --position-size 2000 --detailed
"""
import asyncio
import sys
import aiosqlite
import aiohttp
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

from src.config import DATABASE_PATH

# Polymarket CLOB API
CLOB_API_BASE = "https://clob.polymarket.com"

# Default parameters
DEFAULT_THRESHOLD = 75
DEFAULT_POSITION_SIZE = 2000.0
DEFAULT_SLIPPAGE_PCT = 0.015
DEFAULT_FEE_PCT = 0.005


@dataclass
class AuditedTrade:
    """A trade from the database with its profitability outcome."""
    trade_id: int
    tx_hash: str
    asset_id: str
    side: str
    insider_score: int
    entry_price: float
    trade_amount_usdc: float
    timestamp: datetime
    owner_address: Optional[str]
    # Outcome
    current_price: Optional[float] = None
    gross_roi_pct: Optional[float] = None
    net_pnl_usdc: Optional[float] = None
    is_resolved: bool = False
    outcome: Optional[str] = None  # 'win', 'loss', 'unknown'
    error: Optional[str] = None


async def fetch_current_price(session: aiohttp.ClientSession, asset_id: str) -> Optional[float]:
    """Fetch current/latest price from CLOB API."""
    try:
        url = f"{CLOB_API_BASE}/prices-history"
        params = {"market": asset_id, "interval": "1h", "fidelity": 1}
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            history = data.get("history", [])
            if history:
                return float(history[-1].get("p", 0))
    except Exception:
        pass
    return None


async def run_audit(
    threshold: int = DEFAULT_THRESHOLD,
    position_size: float = DEFAULT_POSITION_SIZE,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
    fee_pct: float = DEFAULT_FEE_PCT,
    detailed: bool = False,
):
    """Run the retroactive profitability audit."""

    print("")
    print("═" * 56)
    print("  RETROACTIVE PROFITABILITY AUDIT")
    print("═" * 56)
    print(f"  Database:         {DATABASE_PATH}")
    print(f"  Score Threshold:  >= {threshold}")
    print(f"  Position Size:    ${position_size:,.0f}")
    print(f"  Slippage Model:   {slippage_pct*100:.1f}%")
    print(f"  Fee Model:        {fee_pct*100:.1f}%")
    print("─" * 56)

    # Load qualifying trades from database
    # Note: the trades table does not have a 'price' column.
    # We estimate entry price from the CLOB API price history at trade time.
    trades: List[AuditedTrade] = []

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, tx_hash, asset_id, side, insider_score,
                   amount_usdc, timestamp, owner_address
            FROM trades
            WHERE insider_score >= ?
              AND asset_id IS NOT NULL
            ORDER BY timestamp ASC
            """,
            (threshold,),
        )
        rows = await cursor.fetchall()

        for row in rows:
            ts = row["timestamp"]
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    ts = datetime.now(timezone.utc)

            trades.append(AuditedTrade(
                trade_id=row["id"],
                tx_hash=row["tx_hash"] or "",
                asset_id=row["asset_id"],
                side=row["side"] or "buy",
                insider_score=row["insider_score"] or 0,
                entry_price=0.0,  # Will be estimated from CLOB API
                trade_amount_usdc=row["amount_usdc"] or 0,
                timestamp=ts,
                owner_address=row["owner_address"],
            ))

    if not trades:
        print(f"  No trades found with insider_score >= {threshold}")
        print("═" * 56)
        return

    # Determine date range
    earliest = min(t.timestamp for t in trades)
    latest = max(t.timestamp for t in trades)
    print(f"  Date Range:       {earliest.strftime('%Y-%m-%d')} → {latest.strftime('%Y-%m-%d')}")
    print(f"  Trades Found:     {len(trades)} (score >= {threshold})")
    print("─" * 56)
    print("  Fetching price histories from CLOB API...")

    # Fetch full price history for each unique asset to determine both entry and current prices
    unique_assets = set(t.asset_id for t in trades)
    history_cache: Dict[str, List[Dict]] = {}

    async with aiohttp.ClientSession() as session:
        for i, asset_id in enumerate(unique_assets):
            try:
                url = f"{CLOB_API_BASE}/prices-history"
                params = {"market": asset_id, "interval": "1h", "fidelity": 60}
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        hist = data.get("history", [])
                        if hist:
                            history_cache[asset_id] = hist
            except Exception:
                pass

            if (i + 1) % 5 == 0:
                print(f"  ... checked {i+1}/{len(unique_assets)} markets")
                await asyncio.sleep(0.5)

    fetched = len(history_cache)
    print(f"  Histories fetched: {fetched}/{len(unique_assets)} markets")
    print("─" * 56)

    # Calculate PnL for each trade using price history
    for trade in trades:
        hist = history_cache.get(trade.asset_id)
        if not hist:
            trade.error = "no_price_data"
            trade.outcome = "unknown"
            continue

        # Find entry price: closest price point to trade timestamp
        trade_ts = trade.timestamp.timestamp() if trade.timestamp.tzinfo else trade.timestamp.replace(tzinfo=timezone.utc).timestamp()
        closest_entry = min(hist, key=lambda h: abs(int(h.get("t", 0)) - trade_ts))
        entry_price = float(closest_entry.get("p", 0))
        if entry_price <= 0:
            trade.error = "zero_entry_price"
            trade.outcome = "unknown"
            continue

        trade.entry_price = entry_price

        # Current price: latest point in history
        current_price = float(hist[-1].get("p", 0))
        trade.current_price = current_price

        # Check if resolved
        if current_price >= 0.95 or current_price <= 0.05:
            trade.is_resolved = True
            current_price = 1.0 if current_price >= 0.95 else 0.0

        # Apply slippage to entry
        if trade.side == "buy":
            entry_with_slip = entry_price * (1 + slippage_pct)
            entry_with_slip = min(entry_with_slip, 0.99)
            if entry_with_slip <= 0:
                trade.outcome = "unknown"
                continue
            gross_roi = (current_price - entry_with_slip) / entry_with_slip * 100
        else:
            entry_with_slip = entry_price * (1 - slippage_pct)
            entry_with_slip = max(entry_with_slip, 0.01)
            gross_roi = (entry_with_slip - current_price) / entry_with_slip * 100

        trade.gross_roi_pct = round(gross_roi, 2)

        # Net PnL on position_size
        fee = position_size * fee_pct
        effective_size = position_size - fee
        shares = effective_size / entry_with_slip

        if trade.side == "buy":
            gross_pnl = (current_price - entry_with_slip) * shares
        else:
            gross_pnl = (entry_with_slip - current_price) * shares

        trade.net_pnl_usdc = round(gross_pnl - fee, 2)
        trade.outcome = "win" if trade.net_pnl_usdc > 0 else "loss"

    # Compute aggregate statistics
    analyzed = [t for t in trades if t.outcome in ("win", "loss")]
    unknown = [t for t in trades if t.outcome == "unknown"]

    if not analyzed:
        print("  Could not fetch price data for any markets.")
        print("  This may happen if the markets have been delisted.")
        print("═" * 56)
        return

    wins = [t for t in analyzed if t.outcome == "win"]
    losses = [t for t in analyzed if t.outcome == "loss"]
    total_pnl = sum(t.net_pnl_usdc for t in analyzed if t.net_pnl_usdc is not None)
    avg_roi = sum(t.gross_roi_pct for t in analyzed if t.gross_roi_pct is not None) / len(analyzed)

    best = max(analyzed, key=lambda t: t.gross_roi_pct or -999)
    worst = min(analyzed, key=lambda t: t.gross_roi_pct or 999)

    resolved_trades = [t for t in analyzed if t.is_resolved]

    # Print aggregate results
    print("  AGGREGATE RESULTS")
    print(f"  Analyzed:         {len(analyzed)} trades ({len(unknown)} unknown)")
    print(f"  Win Rate:         {len(wins)/len(analyzed)*100:.1f}% ({len(wins)}/{len(analyzed)})")
    print(f"  Avg ROI:          {avg_roi:+.1f}%")
    print(f"  Total PnL:        ${total_pnl:+,.2f} (on ${position_size:,.0f}/trade)")
    print(f"  Resolved Markets: {len(resolved_trades)}/{len(analyzed)}")
    print(f"  Best Trade:       {best.gross_roi_pct:+.1f}% ({best.asset_id[:20]}...)")
    print(f"  Worst Trade:      {worst.gross_roi_pct:+.1f}% ({worst.asset_id[:20]}...)")
    print("─" * 56)

    # By score tier
    print("  BY SCORE TIER")
    for tier_min, tier_max, label in [
        (75, 84, "75-84"),
        (85, 94, "85-94"),
        (95, 200, "95+"),
    ]:
        tier_trades = [t for t in analyzed if tier_min <= t.insider_score <= tier_max]
        if tier_trades:
            tier_wins = sum(1 for t in tier_trades if t.outcome == "win")
            tier_avg_roi = sum(t.gross_roi_pct for t in tier_trades if t.gross_roi_pct) / len(tier_trades)
            tier_pnl = sum(t.net_pnl_usdc for t in tier_trades if t.net_pnl_usdc)
            print(
                f"  {label}:  Win {tier_wins/len(tier_trades)*100:.0f}% | "
                f"Avg ROI {tier_avg_roi:+.1f}% | "
                f"PnL ${tier_pnl:+,.0f} | n={len(tier_trades)}"
            )

    # By side
    print("─" * 56)
    print("  BY SIDE")
    for side_name in ["buy", "sell"]:
        side_trades = [t for t in analyzed if t.side == side_name]
        if side_trades:
            side_wins = sum(1 for t in side_trades if t.outcome == "win")
            side_roi = sum(t.gross_roi_pct for t in side_trades if t.gross_roi_pct) / len(side_trades)
            print(
                f"  {side_name.upper()}:  Win {side_wins/len(side_trades)*100:.0f}% | "
                f"Avg ROI {side_roi:+.1f}% | n={len(side_trades)}"
            )

    # Detailed trade log
    if detailed:
        print("─" * 56)
        print("  DETAILED TRADE LOG")
        print(f"  {'#':>4} {'Score':>5} {'Side':>4} {'Entry':>6} {'Now':>6} {'ROI':>8} {'PnL':>10} {'Status':>8}")
        for t in sorted(analyzed, key=lambda x: x.timestamp):
            icon = "[WIN] " if t.outcome == "win" else "[LOSS]"
            status = "RESOLVED" if t.is_resolved else "OPEN"
            print(
                f"  {icon} {t.trade_id:>4} {t.insider_score:>5} {t.side:>4} "
                f"${t.entry_price:.3f} ${(t.current_price or 0):.3f} "
                f"{(t.gross_roi_pct or 0):>+7.1f}% "
                f"${(t.net_pnl_usdc or 0):>+9.2f} {status:>8}"
            )

    print("═" * 56)


async def main():
    """CLI entry point."""
    # Parse args
    threshold = DEFAULT_THRESHOLD
    position_size = DEFAULT_POSITION_SIZE
    detailed = False

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--threshold" and i + 1 < len(sys.argv):
            threshold = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--position-size" and i + 1 < len(sys.argv):
            position_size = float(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--detailed":
            detailed = True
            i += 1
        elif sys.argv[i] in ("--help", "-h"):
            print(__doc__)
            return
        else:
            print(f"Unknown option: {sys.argv[i]}")
            print("Use --help for usage info")
            return

    await run_audit(
        threshold=threshold,
        position_size=position_size,
        detailed=detailed,
    )


if __name__ == "__main__":
    asyncio.run(main())
