"""
Upside Validator - Validates trade opportunities for copy-trading potential.

Performs three checks:
1. Price Ceiling: Discards alerts where current price > 70%
2. Order Book Slippage: Calculates cost of $2k follower trade
3. Alpha Gap: Ensures entry price vs current has sufficient margin
"""
import aiohttp
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime

from ..config import (
    PRICE_CEILING,
    FOLLOWER_TRADE_SIZE,
    MAX_SLIPPAGE_PERCENT,
    SLIPPAGE_SCORE_PENALTY,
    ALPHA_GAP_MIN,
)


@dataclass
class UpsideValidationResult:
    """Result of upside validation checks."""
    is_valid: bool  # True if trade passes all checks
    current_price: float  # Current market price (0-1)
    
    # Individual check results
    passes_price_ceiling: bool
    passes_slippage: bool
    passes_alpha_gap: bool
    
    # Details
    slippage_percent: Optional[float] = None
    alpha_gap: Optional[float] = None
    score_adjustment: int = 0  # Points to add/subtract
    rejection_reasons: List[str] = None
    
    def __post_init__(self):
        if self.rejection_reasons is None:
            self.rejection_reasons = []


class UpsideValidator:
    """
    Validates trades for copy-trading potential.
    
    Checks:
    1. Price Ceiling: Current price must be <= 70% to have upside
    2. Slippage: $2k follower trade must not move price > 3%
    3. Alpha Gap: Entry price vs current must have >= 8 cent gap
    """
    
    CLOB_BASE_URL = "https://clob.polymarket.com"
    
    def __init__(self):
        self.price_ceiling = PRICE_CEILING
        self.follower_trade_size = FOLLOWER_TRADE_SIZE
        self.max_slippage = MAX_SLIPPAGE_PERCENT
        self.slippage_penalty = SLIPPAGE_SCORE_PENALTY
        self.alpha_gap_min = ALPHA_GAP_MIN
    
    async def validate(
        self,
        asset_id: str,
        insider_entry_price: Optional[float] = None,
        side: str = "buy",
    ) -> UpsideValidationResult:
        """
        Validate a trade opportunity.
        
        Args:
            asset_id: The outcome token ID
            insider_entry_price: Price the insider entered at (0-1)
            side: 'buy' or 'sell' - determines which side of book to check
            
        Returns:
            UpsideValidationResult with validation details
        """
        rejection_reasons = []
        score_adjustment = 0
        
        # Fetch order book
        book_data = await self._fetch_order_book(asset_id)
        
        if not book_data:
            # Can't validate without order book - allow but note
            return UpsideValidationResult(
                is_valid=True,
                current_price=0.5,  # Unknown
                passes_price_ceiling=True,
                passes_slippage=True,
                passes_alpha_gap=True,
                rejection_reasons=["Could not fetch order book - validation skipped"],
            )
        
        # Get current price (midpoint of best bid/ask)
        current_price = self._calculate_mid_price(book_data)
        
        # Check 1: Price Ceiling
        passes_price_ceiling = current_price <= self.price_ceiling
        if not passes_price_ceiling:
            rejection_reasons.append(
                f"Price ceiling exceeded: {current_price:.2%} > {self.price_ceiling:.0%}"
            )
        
        # Check 2: Slippage for $2k follower trade
        slippage_percent = self._calculate_slippage(
            book_data, 
            self.follower_trade_size, 
            side
        )
        passes_slippage = slippage_percent <= self.max_slippage
        if not passes_slippage:
            rejection_reasons.append(
                f"High slippage: {slippage_percent:.1%} > {self.max_slippage:.0%} max"
            )
            score_adjustment -= self.slippage_penalty
        
        # Check 3: Alpha Gap (only if we know insider entry price)
        passes_alpha_gap = True
        alpha_gap = None
        
        if insider_entry_price is not None:
            alpha_gap = abs(current_price - insider_entry_price)
            passes_alpha_gap = alpha_gap >= self.alpha_gap_min
            
            if not passes_alpha_gap:
                rejection_reasons.append(
                    f"Alpha gap too small: {alpha_gap:.2f} < {self.alpha_gap_min:.2f} min"
                )
        
        # Overall validity
        is_valid = passes_price_ceiling and passes_alpha_gap
        # Note: Slippage doesn't reject, just penalizes score
        
        return UpsideValidationResult(
            is_valid=is_valid,
            current_price=current_price,
            passes_price_ceiling=passes_price_ceiling,
            passes_slippage=passes_slippage,
            passes_alpha_gap=passes_alpha_gap,
            slippage_percent=slippage_percent,
            alpha_gap=alpha_gap,
            score_adjustment=score_adjustment,
            rejection_reasons=rejection_reasons,
        )
    
    async def _fetch_order_book(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """Fetch order book from CLOB API."""
        url = f"{self.CLOB_BASE_URL}/book"
        params = {"token_id": asset_id}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        print(f"[WARN] CLOB API error: {resp.status}")
                        return None
        except Exception as e:
            print(f"[ERROR] Failed to fetch order book: {e}")
            return None
    
    def _calculate_mid_price(self, book_data: Dict[str, Any]) -> float:
        """Calculate mid price from order book."""
        bids = book_data.get("bids", [])
        asks = book_data.get("asks", [])
        
        best_bid = float(bids[0]["price"]) if bids else 0
        best_ask = float(asks[0]["price"]) if asks else 1
        
        return (best_bid + best_ask) / 2
    
    def _calculate_slippage(
        self, 
        book_data: Dict[str, Any], 
        trade_size_usdc: float,
        side: str
    ) -> float:
        """
        Calculate slippage for a given trade size.
        
        For a BUY order, we walk up the asks.
        For a SELL order, we walk down the bids.
        
        Returns slippage as a percentage (0.03 = 3%)
        """
        if side == "buy":
            orders = book_data.get("asks", [])
        else:
            orders = book_data.get("bids", [])
        
        if not orders:
            return 0.0
        
        # Starting price (best price)
        start_price = float(orders[0]["price"])
        
        # Walk through order book
        remaining_usdc = trade_size_usdc
        total_shares = 0
        weighted_price_sum = 0
        
        for order in orders:
            price = float(order["price"])
            size = float(order["size"])
            
            # How much USDC to fill this level
            level_usdc = price * size
            
            if remaining_usdc <= 0:
                break
            
            if level_usdc <= remaining_usdc:
                # Take entire level
                total_shares += size
                weighted_price_sum += price * size
                remaining_usdc -= level_usdc
            else:
                # Partial fill
                partial_shares = remaining_usdc / price
                total_shares += partial_shares
                weighted_price_sum += price * partial_shares
                remaining_usdc = 0
        
        if total_shares == 0:
            return 0.0
        
        # Average execution price
        avg_price = weighted_price_sum / total_shares
        
        # Slippage = how much we moved from best price
        slippage = abs(avg_price - start_price) / start_price
        
        return slippage


async def test_validator():
    """Test the upside validator."""
    validator = UpsideValidator()
    
    # Test with a known asset ID (you'd need a real one)
    test_asset = "21742633143463906290569050155826241533067272736897614950488156847949938836455"
    
    result = await validator.validate(test_asset)
    print(f"Valid: {result.is_valid}")
    print(f"Current Price: {result.current_price:.2%}")
    print(f"Slippage: {result.slippage_percent:.2%}" if result.slippage_percent else "N/A")
    print(f"Rejections: {result.rejection_reasons}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_validator())
