"""
Late-Stage Sentinel - Detects suspicious activity in mature, stagnant markets.

Logic: If a market is >21 days old AND price has been stable for 48 hours,
any sudden trade >$20k receives a +60 score bonus as this pattern
strongly indicates insider knowledge of upcoming resolution.
"""
import aiohttp
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..config import (
    MATURE_MARKET_DAYS,
    STAGNANT_HOURS,
    LATE_STAGE_TRADE_MIN,
    LATE_STAGE_SCORE_BONUS,
)


@dataclass
class LateStageResult:
    """Result of late-stage analysis."""
    is_late_stage: bool
    score_bonus: int
    
    # Details
    market_age_days: Optional[int] = None
    price_stable_hours: Optional[float] = None
    price_range: Optional[float] = None  # Max price diff in stability window
    reason: Optional[str] = None


class LateStageSentinel:
    """
    Detects suspicious activity in mature, stagnant markets.
    
    A late-stage pattern is detected when:
    1. Market is > 21 days old
    2. Price has been stable (< 5% range) for 48 hours
    3. Trade size is > $20,000
    
    This pattern receives +60 score bonus.
    """
    
    CLOB_BASE_URL = "https://clob.polymarket.com"
    GAMMA_API_URL = "https://gamma-api.polymarket.com"
    STABILITY_THRESHOLD = 0.05  # 5% max price range for "stable"
    
    def __init__(self):
        self.mature_days = MATURE_MARKET_DAYS
        self.stagnant_hours = STAGNANT_HOURS
        self.trade_min = LATE_STAGE_TRADE_MIN
        self.score_bonus = LATE_STAGE_SCORE_BONUS
    
    async def analyze(
        self,
        asset_id: str,
        trade_amount_usdc: float,
    ) -> LateStageResult:
        """
        Analyze if a trade matches the late-stage pattern.
        
        Args:
            asset_id: The outcome token ID
            trade_amount_usdc: Trade size in USDC
            
        Returns:
            LateStageResult with analysis details
        """
        # Check trade size first (cheap check)
        if trade_amount_usdc < self.trade_min:
            return LateStageResult(
                is_late_stage=False,
                score_bonus=0,
                reason=f"Trade ${trade_amount_usdc:,.0f} below ${self.trade_min:,.0f} threshold",
            )
        
        # Get market age
        market_age_days = await self._get_market_age(asset_id)
        
        if market_age_days is None:
            return LateStageResult(
                is_late_stage=False,
                score_bonus=0,
                reason="Could not determine market age",
            )
        
        if market_age_days < self.mature_days:
            return LateStageResult(
                is_late_stage=False,
                score_bonus=0,
                market_age_days=market_age_days,
                reason=f"Market {market_age_days} days old, need >{self.mature_days}",
            )
        
        # Check price stability
        stability = await self._check_price_stability(asset_id)
        
        if stability is None:
            return LateStageResult(
                is_late_stage=False,
                score_bonus=0,
                market_age_days=market_age_days,
                reason="Could not check price stability",
            )
        
        stable_hours, price_range = stability
        
        if stable_hours < self.stagnant_hours:
            return LateStageResult(
                is_late_stage=False,
                score_bonus=0,
                market_age_days=market_age_days,
                price_stable_hours=stable_hours,
                price_range=price_range,
                reason=f"Price stable for {stable_hours:.1f}h, need >{self.stagnant_hours}h",
            )
        
        # All conditions met - late stage pattern!
        return LateStageResult(
            is_late_stage=True,
            score_bonus=self.score_bonus,
            market_age_days=market_age_days,
            price_stable_hours=stable_hours,
            price_range=price_range,
            reason=f"LATE-STAGE: {market_age_days}d old market, {stable_hours:.0f}h stable, ${trade_amount_usdc:,.0f} trade",
        )
    
    async def _get_market_age(self, asset_id: str) -> Optional[int]:
        """Get market age in days from Gamma API."""
        url = f"{self.GAMMA_API_URL}/markets"
        params = {"clob_token_ids": asset_id}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        
                        if markets and len(markets) > 0:
                            market = markets[0]
                            created_at = market.get("createdAt")
                            
                            if created_at:
                                # Parse ISO format
                                created_dt = datetime.fromisoformat(
                                    created_at.replace("Z", "+00:00")
                                )
                                age = datetime.now(created_dt.tzinfo) - created_dt
                                return age.days
                        
                        return None
                    else:
                        return None
        except Exception as e:
            print(f"[WARN] Failed to get market age: {e}")
            return None
    
    async def _check_price_stability(
        self,
        asset_id: str,
    ) -> Optional[tuple[float, float]]:
        """
        Check how long the price has been stable.
        
        Returns:
            Tuple of (hours_stable, price_range) or None on error
        """
        url = f"{self.CLOB_BASE_URL}/prices-history"
        params = {
            "market": asset_id,
            "interval": "1h",  # Hourly candles
            "fidelity": 60,  # Last 60 hours
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        history = data.get("history", [])
                        
                        if not history or len(history) < 2:
                            return None
                        
                        # Work backwards to find when price became unstable
                        # Get the most recent price
                        recent_prices = [float(h.get("p", 0.5)) for h in history[-self.stagnant_hours:]]
                        
                        if not recent_prices:
                            return None
                        
                        # Calculate price range in recent window
                        price_min = min(recent_prices)
                        price_max = max(recent_prices)
                        price_range = price_max - price_min
                        
                        # If the entire window is stable, return full hours
                        if price_range <= self.STABILITY_THRESHOLD:
                            return (len(recent_prices), price_range)
                        
                        # Otherwise, find how many hours from recent are stable
                        stable_count = 0
                        current_price = recent_prices[-1]
                        
                        for i in range(len(recent_prices) - 1, -1, -1):
                            if abs(recent_prices[i] - current_price) <= self.STABILITY_THRESHOLD:
                                stable_count += 1
                            else:
                                break
                        
                        return (stable_count, price_range)
                    else:
                        return None
        except Exception as e:
            print(f"[WARN] Failed to check price stability: {e}")
            return None
