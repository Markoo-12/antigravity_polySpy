"""
Insider Score calculator (V2.0).
Orchestrates all forensic checks and calculates final insider probability score
using weighted features and coordination detection.

Version 2.0 Changes:
- Weighted feature scoring with configurable weights
- Coordination detection multiplier (Sybil cluster detection)
- Linear wallet age decay (50 - Days × 7)
- Round number detection (+15 points)
"""
import json
from datetime import datetime
from typing import List, Optional, Dict
from dataclasses import dataclass, field

from .bridge_detector import BridgeDetector, BridgeDetectionResult
from .win_rate_analyzer import WinRateAnalyzer, WinRateResult
from .market_velocity import MarketVelocityAnalyzer, MarketVelocityResult
from .wallet_analyzer import WalletAnalyzer, WalletAnalysisResult
from .coordination_detector import detect_coordination, CoordinationResult
from ..config import FORENSIC_USDC_THRESHOLD, INSIDER_ALERT_THRESHOLD


# =============================================================================
# FEATURE WEIGHTS (configurable for tuning)
# =============================================================================
FEATURE_WEIGHTS: Dict[str, float] = {
    "bridge_funding": 1.0,      # 40 points max
    "win_rate": 1.0,            # 30 points max
    "quiet_accumulation": 1.0,  # 30 points max
    "new_wallet": 1.0,          # 50 points max (with decay)
    "low_activity": 1.0,        # 20 points max
    "maduro_rule": 1.0,         # 30 points max
    "round_number": 1.0,        # 15 points max
    "binary_concentration": 1.0, # 25 points max (Shadow-Whale)
    "whale_trade": 1.0,         # 10-20 points based on size (NEW)
}


@dataclass
class InsiderScoreResult:
    """Complete insider score analysis result."""
    score: int  # Final weighted score with coordination factor
    base_score: int  # Score before coordination multiplier
    is_alert_worthy: bool  # score >= threshold
    reasons: List[str] = field(default_factory=list)
    
    # Coordination/Sybil detection
    coordination_factor: float = 1.0
    is_coordinated: bool = False
    cluster_wallets: List[str] = field(default_factory=list)
    
    # Individual check results
    bridge_result: Optional[BridgeDetectionResult] = None
    win_rate_result: Optional[WinRateResult] = None
    velocity_result: Optional[MarketVelocityResult] = None
    wallet_result: Optional[WalletAnalysisResult] = None
    coordination_result: Optional[CoordinationResult] = None
    
    # Feature breakdown for transparency
    feature_scores: Dict[str, int] = field(default_factory=dict)
    
    def to_json(self) -> str:
        """Serialize reasons to JSON for database storage."""
        return json.dumps(self.reasons)


class InsiderScorer:
    """
    Calculates insider probability score for a trade (V2.0).
    
    Uses weighted features and coordination detection:
    TotalScore = (Σ(Feature × Weight)) × CoordinationFactor
    
    Score breakdown (can exceed 100):
    - Bridge Funding: +40 points × weight
    - High Win Rate: +30 points × weight
    - Quiet Accumulation: +30 points × weight
    - New Wallet (<7 days): up to +50 points (linear decay) × weight
    - Low Activity (<10 txns): +20 points × weight
    - Single-Market Whale (Maduro Rule): +30 points × weight
    - Round Number Trade: +15 points × weight
    
    Coordination Factor: 1.5× if Sybil cluster detected, else 1.0×
    """
    
    # Threshold for running full wallet analysis
    FULL_ANALYSIS_THRESHOLD = 10000  # $10k
    
    def __init__(self, feature_weights: Optional[Dict[str, float]] = None):
        """
        Initialize scorer with optional custom feature weights.
        
        Args:
            feature_weights: Optional dict to override default weights
        """
        self.bridge_detector = BridgeDetector()
        self.win_rate_analyzer = WinRateAnalyzer()
        self.velocity_analyzer = MarketVelocityAnalyzer()
        self.wallet_analyzer = WalletAnalyzer()
        self.weights = feature_weights or FEATURE_WEIGHTS.copy()
    
    async def calculate_score(
        self,
        owner_address: str,
        trade_timestamp: datetime,
        trade_amount_usdc: float,
        asset_id: str,
        proxy_address: Optional[str] = None,  # NEW: fallback for wallet analysis
    ) -> InsiderScoreResult:
        """
        Calculate the insider probability score for a trade.
        
        Formula: TotalScore = (Σ(Feature × Weight)) × CoordinationFactor
        
        Args:
            owner_address: The resolved owner EOA
            trade_timestamp: When the trade occurred
            trade_amount_usdc: Trade size in USDC
            asset_id: The outcome token asset ID
            proxy_address: The proxy wallet (used as fallback if owner unknown)
            
        Returns:
            InsiderScoreResult with complete analysis
        """
        feature_scores: Dict[str, int] = {}
        reasons: List[str] = []
        
        bridge_result = None
        win_rate_result = None
        velocity_result = None
        wallet_result = None
        coordination_result = None
        
        # Only run forensics on trades above threshold
        if trade_amount_usdc < FORENSIC_USDC_THRESHOLD:
            return InsiderScoreResult(
                score=0,
                base_score=0,
                is_alert_worthy=False,
                reasons=[f"Trade below ${FORENSIC_USDC_THRESHOLD:,} threshold"],
            )
        
        # =====================================================================
        # FEATURE 1: Bridge Funding (+40)
        # =====================================================================
        bridge_result = await self.bridge_detector.check_bridge_funding(
            owner_address,
            trade_timestamp,
        )
        if bridge_result.is_bridge_funded:
            weighted_score = int(bridge_result.score_points * self.weights.get("bridge_funding", 1.0))
            feature_scores["bridge_funding"] = weighted_score
            reasons.append(
                f"Freshly funded from {bridge_result.bridge_name} Bridge "
                f"({bridge_result.hours_before_trade:.1f}h ago) [+{weighted_score}]"
            )
        
        # =====================================================================
        # FEATURE 2: Win Rate (+30)
        # Note: ERC-1155 tokens are held by proxy, not owner, so use proxy_address
        # =====================================================================
        win_rate_address = proxy_address or owner_address
        win_rate_result = await self.win_rate_analyzer.calculate_win_rate(
            win_rate_address,
            lookback_days=30,
        )
        if win_rate_result.score_points > 0:
            weighted_score = int(win_rate_result.score_points * self.weights.get("win_rate", 1.0))
            feature_scores["win_rate"] = weighted_score
            reasons.append(
                f"{win_rate_result.win_rate*100:.0f}% win rate on "
                f"${win_rate_result.total_volume_usdc:,.0f} volume [+{weighted_score}]"
            )
        
        # =====================================================================
        # FEATURE 3: Market Velocity / Quiet Accumulation (+30)
        # =====================================================================
        velocity_result = await self.velocity_analyzer.check_quiet_accumulation(
            trade_amount_usdc,
            asset_id,
        )
        if velocity_result.is_quiet_accumulation:
            weighted_score = int(velocity_result.score_points * self.weights.get("quiet_accumulation", 1.0))
            feature_scores["quiet_accumulation"] = weighted_score
            reasons.append(
                f"Quiet accumulation pattern detected [+{weighted_score}]"
            )
        
        # =====================================================================
        # FEATURE 3.5: Whale Trade Size Bonus (+10/+15/+20)
        # =====================================================================
        # Works without owner resolution - based on trade amount only
        whale_bonus = self._calculate_whale_bonus(trade_amount_usdc)
        if whale_bonus > 0:
            weighted_score = int(whale_bonus * self.weights.get("whale_trade", 1.0))
            feature_scores["whale_trade"] = weighted_score
            reasons.append(
                f"Whale trade (${trade_amount_usdc:,.0f}) [+{weighted_score}]"
            )
        
        # =====================================================================
        # FEATURE 3.6: Round Number Detection (works without owner)
        # =====================================================================
        if self._is_round_number(trade_amount_usdc):
            base_score = 15
            weighted_score = int(base_score * self.weights.get("round_number", 1.0))
            feature_scores["round_number"] = weighted_score
            reasons.append(
                f"Round number trade (${trade_amount_usdc:,.0f}) [+{weighted_score}]"
            )
        
        # =====================================================================
        # FEATURE 4-7: Wallet Analysis (for trades >= threshold)
        # =====================================================================
        # Use owner_address if available, otherwise fall back to proxy_address
        analysis_address = owner_address or proxy_address
        
        if trade_amount_usdc >= self.FULL_ANALYSIS_THRESHOLD:
            # First try owner address if available
            analysis_address = owner_address or proxy_address
            
            if analysis_address:
                wallet_result = await self.wallet_analyzer.analyze_wallet(
                    wallet_address=analysis_address,
                    trade_timestamp=trade_timestamp,
                    current_asset_id=asset_id,
                    trade_amount_usdc=trade_amount_usdc,
                )
                
                # Check for Infrastructure/Factory owner (excessive transactions)
                # If owner has >50k txns, it's likely a factory, exchange, or massive bot.
                # In this case, the specific proxy behavior is more relevant than the owner's.
                if (wallet_result.total_transactions > 50_000 and 
                    owner_address and 
                    proxy_address and 
                    analysis_address == owner_address):
                    
                    print(f"   [INFO] Owner has {wallet_result.total_transactions:,} txns (Likely Infrastructure). Switching to proxy analysis.")
                    
                    # Re-run analysis on the proxy itself
                    wallet_result = await self.wallet_analyzer.analyze_wallet(
                        wallet_address=proxy_address,
                        trade_timestamp=trade_timestamp,
                        current_asset_id=asset_id,
                        trade_amount_usdc=trade_amount_usdc,
                    )
            
            # Feature 4: New Wallet (with linear decay)
            if wallet_result.is_new_wallet and wallet_result.wallet_age_score > 0:
                weighted_score = int(wallet_result.wallet_age_score * self.weights.get("new_wallet", 1.0))
                feature_scores["new_wallet"] = weighted_score
                reasons.append(
                    f"New wallet ({wallet_result.wallet_age_days:.1f} days old) [+{weighted_score}]"
                )
            
            # Feature 5: Low Activity
            if wallet_result.is_low_activity:
                base_score = 20  # SCORE_LOW_TX_COUNT
                weighted_score = int(base_score * self.weights.get("low_activity", 1.0))
                feature_scores["low_activity"] = weighted_score
                reasons.append(
                    f"Low activity ({wallet_result.total_transactions} txns) [+{weighted_score}]"
                )
            
            # Feature 6: Single-Market Whale (Maduro Rule)
            if wallet_result.is_single_market:
                base_score = 30  # SCORE_SINGLE_MARKET
                weighted_score = int(base_score * self.weights.get("maduro_rule", 1.0))
                feature_scores["maduro_rule"] = weighted_score
                reasons.append(
                    f"Single-market whale (Maduro Rule) [+{weighted_score}]"
                )
            
            # Feature 7: Round Number - MOVED to run before wallet analysis
            # (Now runs even without owner resolution)
            
            # Feature 8: Binary Concentration (+25 pts)
            if wallet_result.is_binary_concentration:
                base_score = 25  # SCORE_BINARY_CONCENTRATION
                weighted_score = int(base_score * self.weights.get("binary_concentration", 1.0))
                feature_scores["binary_concentration"] = weighted_score
                reasons.append(
                    f"Binary concentration (100% in single asset) [+{weighted_score}]"
                )
        
        # =====================================================================
        # COORDINATION DETECTION (Sybil Cluster)
        # =====================================================================
        coordination_result = await detect_coordination(
            asset_id=asset_id,
            timestamp=trade_timestamp,
        )
        
        # Calculate base score (sum of weighted features)
        base_score = sum(feature_scores.values())
        
        # Apply coordination factor
        coordination_factor = coordination_result.factor
        final_score = int(base_score * coordination_factor)
        
        # Add coordination info to reasons if applicable
        if coordination_result.is_coordinated:
            reasons.append(
                f"SYBIL CLUSTER: {coordination_result.cluster_size} wallets in "
                f"{coordination_result.time_window_minutes * 2} min window [×{coordination_factor}]"
            )
        
        # Determine if alert-worthy
        is_alert_worthy = final_score >= INSIDER_ALERT_THRESHOLD
        
        return InsiderScoreResult(
            score=final_score,
            base_score=base_score,
            is_alert_worthy=is_alert_worthy,
            reasons=reasons,
            coordination_factor=coordination_factor,
            is_coordinated=coordination_result.is_coordinated,
            cluster_wallets=coordination_result.cluster_wallets,
            bridge_result=bridge_result,
            win_rate_result=win_rate_result,
            velocity_result=velocity_result,
            wallet_result=wallet_result,
            coordination_result=coordination_result,
            feature_scores=feature_scores,
        )
    
    def format_score_summary(self, result: InsiderScoreResult) -> str:
        """Format a human-readable score summary."""
        # Use ASCII for Windows console compatibility
        icon = "[ALERT]" if result.is_alert_worthy else "[SCORE]"
        
        lines = [
            f"{icon} Insider Score: {result.score}/100+",
        ]
        
        # Show base score and coordination factor if different
        if result.coordination_factor != 1.0:
            lines.append(
                f"  Base: {result.base_score} × {result.coordination_factor} (Sybil Cluster)"
            )
        
        if result.reasons:
            lines.append("Reasons:")
            for reason in result.reasons:
                lines.append(f"  - {reason}")
        else:
            lines.append("No flags triggered")
        
        return "\n".join(lines)
    
    def _calculate_whale_bonus(self, trade_amount_usdc: float) -> int:
        """
        Calculate whale trade bonus based on trade size.
        Uses halved values as per user request.
        
        Returns:
            Bonus points: +10 ($50k-$100k), +15 ($100k-$200k), +20 ($200k+)
        """
        if trade_amount_usdc >= 200_000:
            return 20
        elif trade_amount_usdc >= 100_000:
            return 15
        elif trade_amount_usdc >= 50_000:
            return 10
        return 0
    
    def _is_round_number(self, amount: float) -> bool:
        """
        Check if trade amount is a round number.
        Round = divisible by 1000 with no remainder.
        """
        return amount >= 10_000 and (amount % 1000) < 1  # Allow for floating point
    
    def update_weights(self, new_weights: Dict[str, float]) -> None:
        """
        Update feature weights dynamically.
        
        Useful for auto-tuning based on backtest results.
        
        Args:
            new_weights: Dict of feature name -> weight multiplier
        """
        self.weights.update(new_weights)
        print(f"[SCORER] Updated weights: {self.weights}")
