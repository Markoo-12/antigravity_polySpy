"""
Feature engineering for Momentum Ignition detection.

Extracts time-series velocity features from a wallet's trade history
to distinguish between momentum manipulators and informed insiders.
"""
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List

from ..database.repository import Trade, TradeRepository


class MomentumFeatureExtractor:
    """
    Extracts 8 features per trade for the XGBoost momentum classifier.
    
    All features are derived from existing database data — no new API calls.
    """
    
    def __init__(self, repository: TradeRepository):
        self.repository = repository
    
    async def extract(
        self,
        trade: Trade,
        owner_address: str,
    ) -> Dict[str, float]:
        """
        Extract features for a single trade.
        
        Args:
            trade: The current trade being evaluated.
            owner_address: Resolved wallet owner address.
            
        Returns:
            Dictionary of feature_name → float value.
        """
        # Fetch recent trade history for this wallet
        trades_1m = await self.repository.get_recent_wallet_trades(
            wallet_address=owner_address, minutes=1
        )
        trades_30m = await self.repository.get_recent_wallet_trades(
            wallet_address=owner_address, minutes=30
        )
        trades_6h = await self.repository.get_recent_wallet_trades(
            wallet_address=owner_address, minutes=360
        )
        
        features = {}
        
        # 1. Volume per second (velocity of the spike)
        features["volume_per_second"] = self._calc_volume_per_second(trades_1m)
        
        # 2. Price delta over rolling 60-second window
        features["price_delta_60s"] = self._calc_price_delta(trades_1m)
        
        # 3. Historical average hold time (minutes)
        features["avg_hold_time_minutes"] = await self._calc_avg_hold_time(
            owner_address, trades_6h
        )
        
        # 4. Buy/sell ratio in last 30 minutes
        features["buy_sell_ratio"] = self._calc_buy_sell_ratio(trades_30m)
        
        # 5. Trade count in last 60 seconds (burst activity)
        features["trade_count_60s"] = float(len(trades_1m))
        
        # 6. This trade amount vs wallet's average trade amount
        features["amount_vs_avg"] = self._calc_amount_vs_avg(trade, trades_6h)
        
        # 7. Is the trade amount a round number ($X,000)?
        features["is_round_number"] = self._is_round_number(trade.amount_usdc)
        
        # 8. Consecutive same-side trades (momentum signal)
        features["consecutive_same_side"] = self._calc_consecutive_same_side(
            trade, trades_30m
        )
        
        return features
    
    def _calc_volume_per_second(self, trades_1m: List[Trade]) -> float:
        """Total USDC volume in last 60s ÷ 60."""
        if not trades_1m:
            return 0.0
        total_vol = sum(t.amount_usdc for t in trades_1m)
        return total_vol / 60.0
    
    def _calc_price_delta(self, trades_1m: List[Trade]) -> float:
        """Max price − min price across trades in last 60s."""
        prices = [t.price for t in trades_1m if t.price is not None and t.price > 0]
        if len(prices) < 2:
            return 0.0
        return max(prices) - min(prices)
    
    async def _calc_avg_hold_time(
        self, owner_address: str, trades_6h: List[Trade]
    ) -> float:
        """
        Average hold time across the wallet's recent positions.
        Uses buy→sell pairs in the same asset to estimate hold duration.
        """
        # Group trades by asset
        asset_trades: Dict[str, List[Trade]] = {}
        for t in trades_6h:
            asset_trades.setdefault(t.asset_id, []).append(t)
        
        hold_times = []
        for asset_id, asset_list in asset_trades.items():
            buys = [t for t in asset_list if t.side == "buy"]
            sells = [t for t in asset_list if t.side == "sell"]
            
            if buys and sells:
                # Simple approach: time between first buy and first sell
                first_buy = min(buys, key=lambda t: t.timestamp)
                first_sell = min(sells, key=lambda t: t.timestamp)
                
                if first_sell.timestamp > first_buy.timestamp:
                    delta = (first_sell.timestamp - first_buy.timestamp).total_seconds() / 60.0
                    hold_times.append(delta)
        
        if hold_times:
            return float(np.mean(hold_times))
        
        # Fallback: use repository method for a rough estimate
        # If no sell data, assume they're still holding (long hold = good signal)
        return 120.0  # Default 2 hours (indicates holder, not flipper)
    
    def _calc_buy_sell_ratio(self, trades_30m: List[Trade]) -> float:
        """Number of buys ÷ total trades in last 30 minutes."""
        if not trades_30m:
            return 0.5  # Neutral default
        buys = sum(1 for t in trades_30m if t.side == "buy")
        return buys / len(trades_30m)
    
    def _calc_amount_vs_avg(self, trade: Trade, trades_6h: List[Trade]) -> float:
        """Current trade USDC ÷ wallet's average trade USDC."""
        if not trades_6h:
            return 1.0
        avg = np.mean([t.amount_usdc for t in trades_6h])
        if avg <= 0:
            return 1.0
        return trade.amount_usdc / avg
    
    def _is_round_number(self, amount: float) -> float:
        """1.0 if the amount is a round number ($X,000), 0.0 otherwise."""
        if amount >= 1000 and amount % 1000 < 10:
            return 1.0
        if amount >= 100 and amount % 100 < 1:
            return 1.0
        return 0.0
    
    def _calc_consecutive_same_side(
        self, trade: Trade, trades_30m: List[Trade]
    ) -> float:
        """Count consecutive trades on the same side (buy/sell) as current trade."""
        if not trades_30m:
            return 1.0
        
        # Sort by timestamp descending (most recent first)
        sorted_trades = sorted(trades_30m, key=lambda t: t.timestamp, reverse=True)
        
        count = 0
        for t in sorted_trades:
            if t.side == trade.side:
                count += 1
            else:
                break
        
        return float(max(count, 1))
