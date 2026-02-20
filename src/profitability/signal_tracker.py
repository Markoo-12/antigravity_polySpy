"""
Signal Tracker - Records alerts and checks price outcomes over time.

Runs a background task that periodically checks the CLOB API for
price updates on pending signals, calculating PnL at various intervals.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from .signal_repo import SignalRepository, Signal
from ..config import DATABASE_PATH

# Polymarket CLOB API
CLOB_API_BASE = "https://clob.polymarket.com"

# Check intervals (seconds)
DEFAULT_CHECK_INTERVAL = 900  # 15 minutes


class SignalTracker:
    """
    Tracks alert signals and monitors their price outcomes.
    
    Usage:
        tracker = SignalTracker(db_path)
        await tracker.init()
        await tracker.record_signal(...)
        await tracker.start_checker()  # background task
    """

    def __init__(self, db_path: str = DATABASE_PATH, check_interval: int = DEFAULT_CHECK_INTERVAL):
        self.repo = SignalRepository(db_path)
        self.check_interval = check_interval
        self._checker_task: Optional[asyncio.Task] = None
        self._running = False

    async def init(self) -> None:
        """Initialize the signals table."""
        await self.repo.init_table()
        print("[SIGNAL] Signal tracker initialized")

    async def record_signal(
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
        Record a new signal when an alert is fired.
        
        Returns:
            The signal ID.
        """
        signal_id = await self.repo.insert_signal(
            trade_id=trade_id,
            asset_id=asset_id,
            side=side,
            insider_score=insider_score,
            entry_price=entry_price,
            alert_timestamp=alert_timestamp,
            market_slug=market_slug,
            owner_address=owner_address,
            trade_amount_usdc=trade_amount_usdc,
        )
        print(f"   [SIGNAL] Recorded signal #{signal_id} (score={insider_score}, price={entry_price:.4f})")
        return signal_id

    async def start_checker(self) -> None:
        """Start the background price-checking task."""
        self._running = True
        self._checker_task = asyncio.create_task(self._check_loop())
        print(f"[SIGNAL] Background price checker started (interval={self.check_interval}s)")

    async def stop_checker(self) -> None:
        """Stop the background price-checking task."""
        self._running = False
        if self._checker_task:
            self._checker_task.cancel()
            try:
                await self._checker_task
            except asyncio.CancelledError:
                pass
        print("[SIGNAL] Background price checker stopped")

    async def _check_loop(self) -> None:
        """Main loop: periodically check prices for pending signals."""
        while self._running:
            try:
                await self._check_pending_signals()
            except Exception as e:
                print(f"[SIGNAL] Error in price check loop: {e}")
            await asyncio.sleep(self.check_interval)

    async def _check_pending_signals(self) -> None:
        """Fetch current prices for all pending signals and update outcomes."""
        pending = await self.repo.get_pending_signals()
        if not pending:
            return

        now = datetime.now(timezone.utc)
        updated = 0

        for signal in pending:
            try:
                current_price = await self._fetch_current_price(signal.asset_id)
                if current_price is None:
                    continue

                hours_elapsed = (now - signal.alert_timestamp).total_seconds() / 3600

                # Determine which price checkpoint to fill
                price_1h = signal.price_1h
                price_6h = signal.price_6h
                price_24h = signal.price_24h
                price_48h = signal.price_48h
                resolved_price = signal.resolved_price

                if hours_elapsed >= 1 and price_1h is None:
                    price_1h = current_price
                if hours_elapsed >= 6 and price_6h is None:
                    price_6h = current_price
                if hours_elapsed >= 24 and price_24h is None:
                    price_24h = current_price
                if hours_elapsed >= 48 and price_48h is None:
                    price_48h = current_price

                # Check if market has resolved (price near 0 or 1)
                if current_price >= 0.95 or current_price <= 0.05:
                    resolved_price = 1.0 if current_price >= 0.95 else 0.0

                # Update price checkpoints
                await self.repo.update_price_check(
                    signal_id=signal.id,
                    price_1h=price_1h if price_1h != signal.price_1h else None,
                    price_6h=price_6h if price_6h != signal.price_6h else None,
                    price_24h=price_24h if price_24h != signal.price_24h else None,
                    price_48h=price_48h if price_48h != signal.price_48h else None,
                    resolved_price=resolved_price if resolved_price != signal.resolved_price else None,
                )

                # Update PnL calculations
                await self.repo.update_pnl(
                    signal_id=signal.id,
                    entry_price=signal.entry_price,
                    side=signal.side,
                    price_1h=price_1h if price_1h != signal.price_1h else None,
                    price_24h=price_24h if price_24h != signal.price_24h else None,
                    resolved_price=resolved_price if resolved_price != signal.resolved_price else None,
                )

                updated += 1

            except Exception as e:
                print(f"[SIGNAL] Error checking signal #{signal.id}: {e}")

        if updated > 0:
            print(f"[SIGNAL] Updated {updated}/{len(pending)} pending signals")

    async def _fetch_current_price(self, asset_id: str) -> Optional[float]:
        """Fetch the current mid-price from CLOB API."""
        try:
            url = f"{CLOB_API_BASE}/prices-history"
            params = {
                "market": asset_id,
                "interval": "1h",
                "fidelity": 1,
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    history = data.get("history", [])
                    if history:
                        # Return the most recent price
                        latest = history[-1]
                        return float(latest.get("p", 0))
            return None
        except Exception:
            return None

    async def get_report(self, min_score: int = 0) -> str:
        """Generate a human-readable profitability report."""
        stats = await self.repo.get_stats(min_score=min_score)

        lines = [
            "",
            "═" * 56,
            "  SIGNAL TRACKER — PROFITABILITY REPORT",
            "═" * 56,
            f"  Total Signals:    {stats['total_signals']}",
            f"  Resolved:         {stats['resolved']}",
            f"  Pending:          {stats['pending']}",
            "─" * 56,
        ]

        if stats["resolved"] > 0:
            lines.extend([
                "  AGGREGATE RESULTS",
                f"  Win Rate:         {stats['win_rate']:.1f}% ({stats['wins']}/{stats['resolved']})",
                f"  Avg PnL (1h):     {stats['avg_pnl_1h']:+.2f}%",
                f"  Avg PnL (24h):    {stats['avg_pnl_24h']:+.2f}%",
                f"  Avg PnL (Resolved): {stats['avg_pnl_resolved']:+.2f}%",
                f"  Best Trade:       {stats['best_trade']:+.2f}%",
                f"  Worst Trade:      {stats['worst_trade']:+.2f}%",
                "─" * 56,
                "  BY SCORE TIER",
            ])
            for tier, data in stats["by_score_tier"].items():
                if data["total"] > 0:
                    lines.append(
                        f"  {tier}:  Win {data['win_rate']:.0f}% | "
                        f"Avg PnL {data['avg_pnl']:+.1f}% | n={data['total']}"
                    )

        elif stats["total_signals"] > 0:
            lines.append("  No signals resolved yet. Waiting for market outcomes...")
        else:
            lines.append("  No signals recorded yet. Start the sentinel to begin tracking.")

        lines.append("═" * 56)
        return "\n".join(lines)
