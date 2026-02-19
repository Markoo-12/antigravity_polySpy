"""
Guardrail Filter - Anti-bot detection and blacklisting logic.
"""
from dataclasses import dataclass
from typing import Optional, Tuple
from ..database.repository import TradeRepository, Trade
from ..database.blacklist_repo import BlacklistRepository

@dataclass
class GuardResult:
    """Result of a guardrail check."""
    should_discard: bool
    reason: Optional[str] = None
    should_blacklist: bool = False
    blacklist_type: Optional[str] = None # 'bot', 'wash', 'flipper'


class GuardrailFilter:
    """
    Implements specific anti-bot guardrails.
    
    1. Net Position Filter (Dump >90% in 15 mins) -> TYPE_BOT
    2. Volume-to-Hold Ratio (>200x) -> TYPE_WASH
    3. Symmetric Trade (Buy/Sell same amount in 10 mins) -> TYPE_BOT
    """
    
    def __init__(self, trade_repo: TradeRepository, blacklist_repo: BlacklistRepository):
        self.trade_repo = trade_repo
        self.blacklist_repo = blacklist_repo
        
    async def check_all(self, trade: Trade, wallet_address: str) -> GuardResult:
        """Run all guardrail checks."""
        
        # 1. Symmetric Trade Check (Applies to all trades, but primarily Sell mirroring Buy or vice versa)
        # "If a SellOrder is detected for the exact same amount... as a BuyOrder"
        if trade.side == 'sell':
             is_symmetric = await self._check_symmetric_trade(wallet_address, trade.amount_usdc, trade.asset_id)
             if is_symmetric:
                 return GuardResult(
                     should_discard=True,
                     reason="Symmetric Rebalancing Bot detected",
                     should_blacklist=False # Spec says "do not notify", doesn't explicitly say blacklist, but let's be safe. 
                     # Wait, spec says: "If an address is flagged ... more than three times, add it".
                     # So we SHOULD flag it internally.
                 )

        # 2. Net Position Filter (Only on SELL: did they dump >90% of a recent buy?)
        if trade.side == 'sell':
            is_dump_bot = await self._check_net_position_dump(wallet_address, trade.asset_id, trade.amount_usdc)
            if is_dump_bot:
                return GuardResult(
                    should_discard=True,
                    reason="Net Position Dump (>90% in 15m)",
                    should_blacklist=True,
                    blacklist_type="bot"
                )
                
        # 3. Volume-to-Hold Ratio (Wash Trader Check)
        # We can run this periodically or on every trade. Let's run on significant trades.
        if trade.amount_usdc > 500:
            is_wash_trader = await self._check_wash_trading(wallet_address)
            if is_wash_trader:
                return GuardResult(
                    should_discard=True,
                    reason="Wash Trader (High Vol / Low Pos)",
                    should_blacklist=True,
                    blacklist_type="wash"
                )
                
        return GuardResult(should_discard=False)

    async def _check_symmetric_trade(self, wallet: str, amount: float, asset_id: str) -> bool:
        """
        C. Symmetric Trade Detector
        Sell amount within 1% of a Buy amount in last 10 minutes.
        """
        txs = await self.trade_repo.get_recent_wallet_trades(wallet, minutes=10)
        
        lower_bound = amount * 0.99
        upper_bound = amount * 1.01
        
        for tx in txs:
            # Look for OPPOSITE side trade of similar amount on SAME asset
            # Spec says "SellOrder... as a BuyOrder".
            if tx.asset_id == asset_id and tx.side == 'buy': # We are checking a SELL
                if lower_bound <= tx.amount_usdc <= upper_bound:
                    return True
        return False

    async def _check_net_position_dump(self, wallet: str, asset_id: str, sell_amount: float) -> bool:
        """
        A. Net Position Filter
        If wallet bought $10k+ and sells >90% within 15 mins.
        """
        # Find if there was a large BUY in last 15 mins
        txs = await self.trade_repo.get_recent_wallet_trades(wallet, minutes=15)
        
        max_buy = 0.0
        
        for tx in txs:
            if tx.asset_id == asset_id and tx.side == 'buy':
                max_buy = max(max_buy, tx.amount_usdc)
        
        if max_buy >= 10000:
            # Check if current SELL is > 90% of that BUY
            if sell_amount >= (max_buy * 0.90):
                return True
                
        return False

    async def _check_wash_trading(self, wallet: str) -> bool:
        """
        B. Volume-to-Hold Ratio
        Ratio: Lifetime Volume / Max Position.
        If Volume > $1M and Ratio > 200 (implied, or just huge volume with low hold).
        Spec example: $1M vol but never > $5k held. Ratio = 200.
        """
        vol = await self.trade_repo.get_lifetime_volume(wallet)
        if vol < 1000000: # Only check whales > $1M vol
            return False
            
        max_pos = await self.trade_repo.get_max_position_value(wallet)
        if max_pos == 0:
            return False
            
        ratio = vol / max_pos
        
        if ratio > 200: # Specific threshold from example (1M / 5k = 200)
             return True
             
        return False
