"""
Market velocity analyzer for detecting quiet accumulation.
Checks if a trade represents significant volume in a low-activity market.
"""
from dataclasses import dataclass
from typing import Optional

from ..config import (
    VOLUME_SHARE_THRESHOLD,
    PRICE_CHANGE_THRESHOLD,
    SCORE_QUIET_ACCUMULATION,
)


@dataclass
class MarketVelocityResult:
    """Result of market velocity analysis."""
    is_quiet_accumulation: bool
    score_points: int
    analysis_note: str


class MarketVelocityAnalyzer:
    """
    Analyzes if a trade represents "Quiet Accumulation".
    
    Refined Definition (V2.1):
    - Drip Detection: 3+ separate buys in same market within 6 hours
      where price remains within 2-cent range.
    - Legacy "large trade" heuristic REMOVED.
    """
    
    def __init__(self, repository=None):
        self.repository = repository
    
    async def check_quiet_accumulation(
        self,
        trade_amount_usdc: float,
        asset_id: str,
        price: Optional[float],
        owner_address: Optional[str] = None,
    ) -> MarketVelocityResult:
        """
        Check for Quiet Accumulation (Drip Detection).
        
        Rule: If wallet makes 3+ separate buys in the same market within 6 hours,
        and the price remains within a 2-cent range, assign +35 points.
        """
        # We need repository, valid owner, and valid price to run this check
        if not self.repository or not owner_address or price is None or price <= 0:
            return MarketVelocityResult(False, 0, "Insufficient data for accumulation check")
        
        return await self._check_drip_detection(owner_address, asset_id, price)

    async def _check_drip_detection(
        self,
        owner_address: str,
        asset_id: str,
        current_price: float
    ) -> MarketVelocityResult:
        """
        Implement Drip Detection logic.
        """
        # Valid price range: +/- 1 cent from current execution price (Total 2 cent range)
        # OR just check that max_price - min_price <= 0.02 in the window
        
        # We'll query last 6 hours of buys for this wallet/asset
        try:
            trades = await self.repository.get_recent_wallet_trades(
                wallet_address=owner_address,
                minutes=360 # 6 hours
            )
            
            # Filter for buys of the specific asset
            relevant_buys = [
                t for t in trades 
                if t.asset_id == asset_id and t.side == 'buy' and t.price is not None
            ]
            
            # Need at least 2 previous buys (plus current one makes 3+)
            # The current trade might not be in DB yet depending on when this runs.
            # Usually forensic runs BEFORE insert? No, listener inserts THEN calls on_trade.
            # So current trade SHOULD be in `trades` list.
            
            if len(relevant_buys) < 3:
                return MarketVelocityResult(False, 0, f"Not enough history ({len(relevant_buys)} buys)")
                
            # Check price consistency
            prices = [t.price for t in relevant_buys]
            min_p = min(prices)
            max_p = max(prices)
            range_cents = max_p - min_p
            
            # 2 cent range check (0.02)
            if range_cents <= 0.02:
                return MarketVelocityResult(
                    is_quiet_accumulation=True,
                    score_points=35, # +35 points
                    analysis_note=f"Drip Detection: {len(relevant_buys)} buys in 6h within ${range_cents:.3f} range"
                )
            
            return MarketVelocityResult(False, 0, f"Prices too volatile (Range: ${range_cents:.3f})")
            
        except Exception as e:
            print(f"[ERROR] Drip detection error: {e}")
            return MarketVelocityResult(False, 0, "Error checking drip detection")

