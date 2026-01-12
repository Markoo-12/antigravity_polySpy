"""
Polymarket Insider Sentinel - Backtest Script
Audits historical markets for "Informed Trading" patterns.

Uses Polymarket CLOB REST API for historical data (no RPC required).

Usage:
    py backtest.py --market <clob_token_id> --days 7
    py backtest.py --market <clob_token_id> --start 2026-01-01 --end 2026-01-10
    py backtest.py --from-db  # Analyze trades from local database
    py backtest.py --demo     # Run with sample data
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from src.config import (
    DATABASE_PATH,
    SCORE_BRIDGE_FUNDED,
    SCORE_HIGH_WIN_RATE,
    SCORE_QUIET_ACCUMULATION,
)
from src.database import init_database, TradeRepository
from src.forensic.bridge_detector import BridgeDetector

# Polymarket CLOB API base URL
CLOB_API_BASE = "https://clob.polymarket.com"


@dataclass
class TradeRecord:
    """Represents a historical trade for backtest analysis."""
    tx_hash: str
    timestamp: datetime
    wallet_address: str  # Maker or Taker address
    side: str  # 'buy' or 'sell'
    amount_usdc: float
    price: float  # Probability (0-1)
    asset_id: str
    trade_type: str = "TAKER"  # TAKER or MAKER


@dataclass
class PricePoint:
    """A single price point from price history."""
    timestamp: datetime
    price: float


@dataclass
class CliffEvent:
    """Represents a price cliff (sudden large move)."""
    timestamp: datetime
    price_before: float
    price_after: float
    price_change_pct: float


@dataclass
class SuspectWallet:
    """A wallet flagged for potential insider activity."""
    address: str
    trade_amount_usdc: float
    trade_timestamp: datetime
    minutes_before_cliff: float
    entry_price: float
    exit_price: float
    roi_pct: float
    potential_profit_usdc: float
    bridge_funded: bool
    bridge_name: Optional[str]
    insider_score: int
    score_breakdown: Dict[str, int]
    
    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "wallet_address": self.address,
            "trade_amount_usdc": round(self.trade_amount_usdc, 2),
            "trade_timestamp": self.trade_timestamp.isoformat(),
            "minutes_before_cliff": round(self.minutes_before_cliff, 1),
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4),
            "roi_pct": round(self.roi_pct, 2),
            "potential_profit_usdc": round(self.potential_profit_usdc, 2),
            "bridge_funded": self.bridge_funded,
            "bridge_name": self.bridge_name,
            "insider_score": self.insider_score,
            "score_breakdown": self.score_breakdown,
        }


class BacktestAnalyzer:
    """
    Analyzes historical Polymarket trades for insider patterns.
    Uses Polymarket CLOB REST API instead of on-chain RPC.
    """
    
    # Analysis parameters
    CLIFF_THRESHOLD_PCT = 0.30  # 30% price jump = cliff
    PRE_CLIFF_WINDOW_HOURS = 12  # Look at trades 12h before cliff
    MIN_TRADE_SIZE_USDC = 5000  # Only analyze trades > $5k
    HIGH_EXIT_PROBABILITY = 0.90  # 90% probability = likely win
    
    def __init__(self):
        self.bridge_detector = BridgeDetector()
        self.trades: List[TradeRecord] = []
        self.price_history: List[PricePoint] = []
        self.cliff: Optional[CliffEvent] = None
        self.suspects: List[SuspectWallet] = []
        self.asset_id: Optional[str] = None
    
    async def load_price_history(
        self,
        asset_id: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        interval: str = "max",
        fidelity: int = 60  # 60 minutes = hourly
    ) -> int:
        """
        Load price history from Polymarket CLOB API.
        
        Args:
            asset_id: The CLOB token ID
            start_ts: Unix timestamp for start (optional)
            end_ts: Unix timestamp for end (optional)
            interval: Predefined interval (1h, 6h, 1d, 1w, 1m, max)
            fidelity: Resolution in minutes
            
        Returns:
            Number of price points loaded
        """
        self.asset_id = asset_id
        
        url = f"{CLOB_API_BASE}/prices-history"
        params = {
            "market": asset_id,
            "fidelity": fidelity,
        }
        
        if start_ts and end_ts:
            params["startTs"] = start_ts
            params["endTs"] = end_ts
        else:
            params["interval"] = interval
        
        print(f"[CLOB] Fetching price history for {asset_id[:20]}...")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    print(f"[ERROR] CLOB API error: {resp.status} - {error}")
                    return 0
                
                data = await resp.json()
                history = data.get("history", [])
                
                for point in history:
                    ts = point.get("t")
                    price = float(point.get("p", 0))
                    
                    if ts:
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                        self.price_history.append(PricePoint(
                            timestamp=dt,
                            price=price
                        ))
        
        self.price_history.sort(key=lambda p: p.timestamp)
        print(f"[CLOB] Loaded {len(self.price_history)} price points")
        
        return len(self.price_history)
    
    async def load_trades_from_clob(
        self,
        asset_id: str,
        limit: int = 500
    ) -> int:
        """
        Load trades from Polymarket CLOB API.
        Note: The /data/trades endpoint requires authentication.
        This is a simplified version that works with public data.
        
        For full trade data, we use the timeseries endpoint and
        estimate trade volumes from price movements.
        
        Args:
            asset_id: The CLOB token ID
            limit: Max trades to fetch
            
        Returns:
            Number of trades loaded (estimated from price data)
        """
        # Since /data/trades requires auth, we'll use price history
        # and simulate trade records based on price movements
        # In production, you'd use the authenticated endpoint
        
        if not self.price_history:
            await self.load_price_history(asset_id)
        
        # Generate synthetic trade records from price movements
        # This is a heuristic - real implementation would use /data/trades
        for i in range(1, len(self.price_history)):
            prev = self.price_history[i - 1]
            curr = self.price_history[i]
            
            price_change = curr.price - prev.price
            
            # Estimate trade from price change direction and magnitude
            if abs(price_change) > 0.01:  # Significant movement
                side = "buy" if price_change > 0 else "sell"
                # Estimate volume based on price change (rough heuristic)
                estimated_volume = abs(price_change) * 100000
                
                self.trades.append(TradeRecord(
                    tx_hash=f"synthetic_{i}",
                    timestamp=curr.timestamp,
                    wallet_address=f"0xSynthetic{i:04d}",
                    side=side,
                    amount_usdc=estimated_volume,
                    price=curr.price,
                    asset_id=asset_id,
                ))
        
        print(f"[CLOB] Generated {len(self.trades)} trade estimates from price data")
        return len(self.trades)
    
    async def load_trades_from_db(self, asset_id: Optional[str] = None) -> int:
        """Load trades from local SQLite database."""
        import aiosqlite
        
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            
            if asset_id:
                cursor = await db.execute(
                    """SELECT * FROM trades WHERE asset_id = ? ORDER BY timestamp""",
                    (asset_id,)
                )
            else:
                cursor = await db.execute(
                    """SELECT * FROM trades ORDER BY timestamp"""
                )
            
            rows = await cursor.fetchall()
            
            for row in rows:
                self.trades.append(TradeRecord(
                    tx_hash=row["tx_hash"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    wallet_address=row["owner_address"] or row["proxy_address"],
                    side=row["side"],
                    amount_usdc=row["amount_usdc"],
                    price=0.5,  # Would need real price data
                    asset_id=row["asset_id"],
                ))
        
        print(f"[DATA] Loaded {len(self.trades)} trades from database")
        return len(self.trades)
    
    def detect_cliff(self) -> Optional[CliffEvent]:
        """
        Detect the "Price Cliff" from price history.
        Looks for sudden price jumps > 30%.
        
        Returns:
            CliffEvent if detected, None otherwise
        """
        if not self.price_history:
            print("[WARN] No price history loaded, using trade-based detection")
            return self._detect_cliff_from_trades()
        
        print(f"[CLIFF] Analyzing {len(self.price_history)} price points for cliffs...")
        
        # Look for sudden jumps in price
        for i in range(1, len(self.price_history)):
            prev = self.price_history[i - 1]
            curr = self.price_history[i]
            
            # Calculate percentage change
            if prev.price > 0:
                pct_change = (curr.price - prev.price) / prev.price
            else:
                pct_change = 0
            
            # Check if this is a cliff
            if abs(pct_change) >= self.CLIFF_THRESHOLD_PCT:
                cliff = CliffEvent(
                    timestamp=curr.timestamp,
                    price_before=prev.price,
                    price_after=curr.price,
                    price_change_pct=pct_change * 100
                )
                self.cliff = cliff
                
                direction = "+" if pct_change > 0 else ""
                print(f"[CLIFF] Detected at {cliff.timestamp}")
                print(f"        Price: {prev.price:.2%} -> {curr.price:.2%} ({direction}{cliff.price_change_pct:.1f}%)")
                return cliff
        
        # If no major cliff found, use the maximum price point
        if self.price_history:
            max_point = max(self.price_history, key=lambda p: p.price)
            min_before_max = min(
                [p for p in self.price_history if p.timestamp < max_point.timestamp],
                key=lambda p: p.price,
                default=self.price_history[0]
            )
            
            if min_before_max.price > 0:
                pct_change = (max_point.price - min_before_max.price) / min_before_max.price * 100
            else:
                pct_change = 0
            
            cliff = CliffEvent(
                timestamp=max_point.timestamp,
                price_before=min_before_max.price,
                price_after=max_point.price,
                price_change_pct=pct_change
            )
            self.cliff = cliff
            print(f"[CLIFF] Using max price point as cliff: {max_point.timestamp}")
            print(f"        Price: {min_before_max.price:.2%} -> {max_point.price:.2%} (+{pct_change:.1f}%)")
            return cliff
        
        return None
    
    def _detect_cliff_from_trades(self) -> Optional[CliffEvent]:
        """Fallback cliff detection using trade data."""
        if not self.trades:
            return None
        
        self.trades.sort(key=lambda t: t.timestamp)
        
        # Use volume imbalance to detect cliff
        windows = {}
        for trade in self.trades:
            key = trade.timestamp.replace(second=0, microsecond=0).isoformat()
            if key not in windows:
                windows[key] = {"buy": 0, "sell": 0, "ts": trade.timestamp}
            windows[key][trade.side] += trade.amount_usdc
        
        window_list = sorted(windows.items(), key=lambda x: x[1]["ts"])
        
        for i in range(1, len(window_list)):
            prev = window_list[i - 1][1]
            curr = window_list[i][1]
            
            prev_total = prev["buy"] + prev["sell"]
            if prev_total > 0 and curr["buy"] > prev_total * 3:
                cliff = CliffEvent(
                    timestamp=curr["ts"],
                    price_before=0.5,
                    price_after=0.8,
                    price_change_pct=60.0
                )
                self.cliff = cliff
                return cliff
        
        # Default to last trade
        if self.trades:
            cliff = CliffEvent(
                timestamp=self.trades[-1].timestamp,
                price_before=0.5,
                price_after=0.95,
                price_change_pct=90.0
            )
            self.cliff = cliff
            return cliff
        
        return None
    
    async def analyze_pre_cliff_trades(self) -> List[SuspectWallet]:
        """
        Analyze trades that occurred before the cliff.
        Filter for large trades and calculate potential profits.
        """
        if not self.cliff:
            print("[ERROR] No cliff detected, cannot analyze")
            return []
        
        window_start = self.cliff.timestamp - timedelta(hours=self.PRE_CLIFF_WINDOW_HOURS)
        
        # Filter pre-cliff trades
        pre_cliff_trades = [
            t for t in self.trades
            if window_start <= t.timestamp < self.cliff.timestamp
            and t.amount_usdc >= self.MIN_TRADE_SIZE_USDC
            and t.side == "buy"
        ]
        
        print(f"[ANALYSIS] Found {len(pre_cliff_trades)} pre-cliff trades > ${self.MIN_TRADE_SIZE_USDC:,}")
        
        # Group by wallet
        wallet_trades: Dict[str, List[TradeRecord]] = {}
        for trade in pre_cliff_trades:
            if trade.wallet_address not in wallet_trades:
                wallet_trades[trade.wallet_address] = []
            wallet_trades[trade.wallet_address].append(trade)
        
        suspects = []
        for wallet, trades in wallet_trades.items():
            total_amount = sum(t.amount_usdc for t in trades)
            earliest_trade = min(trades, key=lambda t: t.timestamp)
            
            time_diff = self.cliff.timestamp - earliest_trade.timestamp
            minutes_before = time_diff.total_seconds() / 60
            
            avg_entry_price = sum(t.price for t in trades) / len(trades) if trades else 0.5
            exit_price = self.cliff.price_after
            
            if avg_entry_price > 0:
                roi = (exit_price - avg_entry_price) / avg_entry_price * 100
                potential_profit = total_amount * (exit_price / avg_entry_price - 1)
            else:
                roi = 0
                potential_profit = 0
            
            # Check bridge funding
            bridge_result = await self.bridge_detector.check_bridge_funding(
                wallet, earliest_trade.timestamp, limit=10
            )
            
            # Calculate insider score
            score = 0
            breakdown = {}
            
            if bridge_result.is_bridge_funded:
                score += SCORE_BRIDGE_FUNDED
                breakdown["bridge_funded"] = SCORE_BRIDGE_FUNDED
            
            if total_amount >= 10000:
                score += SCORE_QUIET_ACCUMULATION
                breakdown["large_position"] = SCORE_QUIET_ACCUMULATION
            
            if minutes_before <= 120:
                extra = 20
                score += extra
                breakdown["timing_suspicious"] = extra
            
            if roi > 50:
                extra = 10
                score += extra
                breakdown["high_roi"] = extra
            
            suspect = SuspectWallet(
                address=wallet,
                trade_amount_usdc=total_amount,
                trade_timestamp=earliest_trade.timestamp,
                minutes_before_cliff=minutes_before,
                entry_price=avg_entry_price,
                exit_price=exit_price,
                roi_pct=roi,
                potential_profit_usdc=potential_profit,
                bridge_funded=bridge_result.is_bridge_funded,
                bridge_name=bridge_result.bridge_name,
                insider_score=min(score, 100),
                score_breakdown=breakdown,
            )
            suspects.append(suspect)
        
        suspects.sort(key=lambda s: s.potential_profit_usdc, reverse=True)
        self.suspects = suspects
        return suspects
    
    def calculate_optimal_threshold(self) -> Dict:
        """Determine optimal score threshold."""
        if not self.suspects:
            return {"error": "No suspects analyzed"}
        
        by_profit = sorted(self.suspects, key=lambda s: s.potential_profit_usdc, reverse=True)
        thresholds = [50, 60, 70, 80, 85, 90]
        results = {}
        
        for threshold in thresholds:
            flagged = [s for s in self.suspects if s.insider_score >= threshold]
            top3 = by_profit[:3]
            top3_caught = sum(1 for s in top3 if s.insider_score >= threshold)
            false_positives = sum(1 for s in flagged if s.potential_profit_usdc < 5000)
            
            results[threshold] = {
                "threshold": threshold,
                "top3_caught": top3_caught,
                "total_flagged": len(flagged),
                "false_positives": false_positives,
                "efficiency": top3_caught / max(len(flagged), 1),
            }
        
        optimal = None
        for t, r in results.items():
            if r["top3_caught"] >= 3 and r["false_positives"] <= 5:
                if optimal is None or r["total_flagged"] < results[optimal]["total_flagged"]:
                    optimal = t
        
        if optimal is None:
            optimal = max(results.keys(), key=lambda t: results[t]["efficiency"])
        
        return {
            "optimal_threshold": optimal,
            "threshold_analysis": results,
            "recommendation": f"Use threshold {optimal} to catch {results[optimal]['top3_caught']}/3 top traders with {results[optimal]['false_positives']} false positives",
        }
    
    def generate_results(self, output_path: str = "results.json") -> str:
        """Generate results.json with complete analysis."""
        threshold_analysis = self.calculate_optimal_threshold()
        
        results = {
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "asset_id": self.asset_id,
            "market_summary": {
                "total_price_points": len(self.price_history),
                "total_trades_analyzed": len(self.trades),
                "cliff_timestamp": self.cliff.timestamp.isoformat() if self.cliff else None,
                "cliff_price_change_pct": self.cliff.price_change_pct if self.cliff else None,
            },
            "pre_cliff_trades": {
                "window_hours": self.PRE_CLIFF_WINDOW_HOURS,
                "min_trade_size_usdc": self.MIN_TRADE_SIZE_USDC,
                "total_suspicious_wallets": len(self.suspects),
            },
            "threshold_analysis": threshold_analysis,
            "suspect_wallets": [s.to_dict() for s in self.suspects[:20]],
            "top_3_most_profitable": [s.to_dict() for s in sorted(
                self.suspects, key=lambda s: s.potential_profit_usdc, reverse=True
            )[:3]],
        }
        
        output_file = Path(output_path)
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)
        
        print(f"\n[OUTPUT] Results saved to {output_file.absolute()}")
        return str(output_file.absolute())
    
    def print_summary(self):
        """Print a human-readable summary."""
        print("\n" + "=" * 60)
        print("BACKTEST ANALYSIS RESULTS")
        print("=" * 60)
        
        if self.asset_id:
            print(f"[MARKET] {self.asset_id[:30]}...")
        
        if self.cliff:
            print(f"[CLIFF] Event: {self.cliff.timestamp}")
            print(f"        Price: {self.cliff.price_before:.2%} -> {self.cliff.price_after:.2%}")
            print(f"        Change: {'+' if self.cliff.price_change_pct > 0 else ''}{self.cliff.price_change_pct:.1f}%")
        
        print(f"\n[DATA] Price Points: {len(self.price_history)}")
        print(f"[DATA] Trades Analyzed: {len(self.trades)}")
        print(f"[ALERT] Suspicious Wallets: {len(self.suspects)}")
        
        if self.suspects:
            print("\n[TOP 3] Most Profitable Pre-Cliff Traders:")
            top3 = sorted(self.suspects, key=lambda s: s.potential_profit_usdc, reverse=True)[:3]
            for i, s in enumerate(top3, 1):
                print(f"   {i}. {s.address[:16]}...")
                print(f"      Amount: ${s.trade_amount_usdc:,.2f}")
                print(f"      Time to Cliff: {s.minutes_before_cliff:.0f} min")
                print(f"      Potential Profit: ${s.potential_profit_usdc:,.2f}")
                print(f"      Insider Score: {s.insider_score}/100")
                if s.bridge_funded:
                    print(f"      [!] Bridge Funded: {s.bridge_name}")
            
            threshold_analysis = self.calculate_optimal_threshold()
            print(f"\n[RECOMMENDATION] {threshold_analysis['recommendation']}")


async def run_demo():
    """Run demo with real CLOB data from a sample market."""
    print("[DEMO] Running backtest with Polymarket CLOB API...\n")
    
    analyzer = BacktestAnalyzer()
    
    # Use a sample token ID (you can replace with any valid CLOB token)
    sample_token = "4370640939548049160201828166293191503482026064792352053936490018661409706941"
    
    # Load price history from last week
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    
    await analyzer.load_price_history(
        asset_id=sample_token,
        start_ts=int(week_ago.timestamp()),
        end_ts=int(now.timestamp()),
        fidelity=60  # Hourly
    )
    
    # Detect cliff from price data
    analyzer.detect_cliff()
    
    # Generate synthetic trades (real implementation would use /data/trades)
    await analyzer.load_trades_from_clob(sample_token)
    
    # Analyze
    await analyzer.analyze_pre_cliff_trades()
    
    # Print results
    analyzer.print_summary()
    analyzer.generate_results("results.json")


async def run_market_analysis(
    asset_id: str,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Run full analysis on a specific market."""
    print(f"[MARKET] Analyzing {asset_id[:30]}...\n")
    
    analyzer = BacktestAnalyzer()
    
    # Determine time range
    now = datetime.now(timezone.utc)
    
    if start_date and end_date:
        start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
    elif days:
        end = now
        start = now - timedelta(days=days)
    else:
        # Default: last 30 days
        end = now
        start = now - timedelta(days=30)
    
    print(f"[TIME] {start.date()} to {end.date()}")
    
    # Load price history
    await analyzer.load_price_history(
        asset_id=asset_id,
        start_ts=int(start.timestamp()),
        end_ts=int(end.timestamp()),
        fidelity=60
    )
    
    if not analyzer.price_history:
        print("[ERROR] No price data found for this market")
        return
    
    # Detect cliff
    analyzer.detect_cliff()
    
    # Generate trades from price data
    await analyzer.load_trades_from_clob(asset_id)
    
    # Analyze
    await analyzer.analyze_pre_cliff_trades()
    
    # Results
    analyzer.print_summary()
    analyzer.generate_results("results.json")


async def main():
    """Main entry point."""
    if len(sys.argv) > 1:
        if sys.argv[1] == "--demo":
            await run_demo()
            return
        
        elif sys.argv[1] == "--from-db":
            analyzer = BacktestAnalyzer()
            asset_id = sys.argv[2] if len(sys.argv) > 2 else None
            await init_database(DATABASE_PATH)
            count = await analyzer.load_trades_from_db(asset_id)
            
            if count == 0:
                print("[ERROR] No trades found in database")
                return
            
            analyzer.detect_cliff()
            await analyzer.analyze_pre_cliff_trades()
            analyzer.print_summary()
            analyzer.generate_results("results.json")
            return
        
        elif sys.argv[1] == "--market":
            if len(sys.argv) < 3:
                print("Usage: py backtest.py --market <clob_token_id> [--days N] [--start YYYY-MM-DD --end YYYY-MM-DD]")
                return
            
            asset_id = sys.argv[2]
            days = None
            start_date = None
            end_date = None
            
            # Parse optional args
            i = 3
            while i < len(sys.argv):
                if sys.argv[i] == "--days" and i + 1 < len(sys.argv):
                    days = int(sys.argv[i + 1])
                    i += 2
                elif sys.argv[i] in ("--start", "--from-date") and i + 1 < len(sys.argv):
                    start_date = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] in ("--end", "--to-date") and i + 1 < len(sys.argv):
                    end_date = sys.argv[i + 1]
                    i += 2
                else:
                    i += 1
            
            await run_market_analysis(asset_id, days, start_date, end_date)
            return
        
        else:
            print("Unknown option. Use --demo, --from-db, or --market")
            return
    
    else:
        await run_demo()


if __name__ == "__main__":
    asyncio.run(main())
