"""
Insider Score calculator.
Orchestrates all forensic checks and calculates final insider probability score.
"""
import json
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass, field

from .bridge_detector import BridgeDetector, BridgeDetectionResult
from .win_rate_analyzer import WinRateAnalyzer, WinRateResult
from .market_velocity import MarketVelocityAnalyzer, MarketVelocityResult
from .wallet_analyzer import WalletAnalyzer, WalletAnalysisResult
from ..config import FORENSIC_USDC_THRESHOLD, INSIDER_ALERT_THRESHOLD


@dataclass
class InsiderScoreResult:
    """Complete insider score analysis result."""
    score: int  # 0-100+
    is_alert_worthy: bool  # score >= threshold
    reasons: List[str] = field(default_factory=list)
    
    # Individual check results
    bridge_result: Optional[BridgeDetectionResult] = None
    win_rate_result: Optional[WinRateResult] = None
    velocity_result: Optional[MarketVelocityResult] = None
    wallet_result: Optional[WalletAnalysisResult] = None
    
    def to_json(self) -> str:
        """Serialize reasons to JSON for database storage."""
        return json.dumps(self.reasons)


class InsiderScorer:
    """
    Calculates insider probability score for a trade.
    
    Score breakdown (can exceed 100):
    - Bridge Funding: +40 points
    - High Win Rate: +30 points
    - Quiet Accumulation: +30 points
    - New Wallet (<7 days): +50 points
    - Low Activity (<10 txns): +20 points
    - Single-Market Whale (Maduro Rule): +30 points
    """
    
    # Threshold for running full wallet analysis (lower to catch more insiders)
    FULL_ANALYSIS_THRESHOLD = 25000  # $25k (lowered from $50k)
    
    def __init__(self):
        self.bridge_detector = BridgeDetector()
        self.win_rate_analyzer = WinRateAnalyzer()
        self.velocity_analyzer = MarketVelocityAnalyzer()
        self.wallet_analyzer = WalletAnalyzer()
    
    async def calculate_score(
        self,
        owner_address: str,
        trade_timestamp: datetime,
        trade_amount_usdc: float,
        asset_id: str,
    ) -> InsiderScoreResult:
        """
        Calculate the insider probability score for a trade.
        
        Args:
            owner_address: The resolved owner EOA
            trade_timestamp: When the trade occurred
            trade_amount_usdc: Trade size in USDC
            asset_id: The outcome token asset ID
            
        Returns:
            InsiderScoreResult with complete analysis
        """
        score = 0
        reasons = []
        
        bridge_result = None
        win_rate_result = None
        velocity_result = None
        wallet_result = None
        
        # Only run forensics on trades above threshold
        if trade_amount_usdc < FORENSIC_USDC_THRESHOLD:
            return InsiderScoreResult(
                score=0,
                is_alert_worthy=False,
                reasons=[f"Trade below ${FORENSIC_USDC_THRESHOLD:,} threshold"],
            )
        
        # Check 1: Bridge Funding (+40)
        bridge_result = await self.bridge_detector.check_bridge_funding(
            owner_address,
            trade_timestamp,
        )
        if bridge_result.is_bridge_funded:
            score += bridge_result.score_points
            reasons.append(
                f"Freshly funded from {bridge_result.bridge_name} Bridge "
                f"({bridge_result.hours_before_trade:.1f}h ago) [+{bridge_result.score_points}]"
            )
        
        # Check 2: Win Rate (+30)
        win_rate_result = await self.win_rate_analyzer.calculate_win_rate(
            owner_address,
            lookback_days=30,
        )
        if win_rate_result.score_points > 0:
            score += win_rate_result.score_points
            reasons.append(
                f"{win_rate_result.win_rate*100:.0f}% win rate on "
                f"${win_rate_result.total_volume_usdc:,.0f} volume [+{win_rate_result.score_points}]"
            )
        
        # Check 3: Market Velocity (+30)
        velocity_result = await self.velocity_analyzer.check_quiet_accumulation(
            trade_amount_usdc,
            asset_id,
        )
        if velocity_result.is_quiet_accumulation:
            score += velocity_result.score_points
            reasons.append(
                f"Quiet accumulation pattern detected [+{velocity_result.score_points}]"
            )
        
        # Check 4: NEW WALLET ANALYSIS (for trades >= $50k)
        if trade_amount_usdc >= self.FULL_ANALYSIS_THRESHOLD:
            wallet_result = await self.wallet_analyzer.analyze_wallet(
                wallet_address=owner_address,
                trade_timestamp=trade_timestamp,
                current_asset_id=asset_id,
            )
            
            if wallet_result.score_points > 0:
                score += wallet_result.score_points
                
                # Add individual reasons
                if wallet_result.is_new_wallet:
                    reasons.append(
                        f"New wallet ({wallet_result.wallet_age_days:.1f} days old) [+50]"
                    )
                
                if wallet_result.is_low_activity:
                    reasons.append(
                        f"Low activity ({wallet_result.total_transactions} txns) [+20]"
                    )
                
                if wallet_result.is_single_market:
                    reasons.append(
                        f"Single-market whale (Maduro Rule) [+30]"
                    )
        
        # Determine if alert-worthy
        is_alert_worthy = score >= INSIDER_ALERT_THRESHOLD
        
        return InsiderScoreResult(
            score=score,
            is_alert_worthy=is_alert_worthy,
            reasons=reasons,
            bridge_result=bridge_result,
            win_rate_result=win_rate_result,
            velocity_result=velocity_result,
            wallet_result=wallet_result,
        )
    
    def format_score_summary(self, result: InsiderScoreResult) -> str:
        """Format a human-readable score summary."""
        # Use ASCII for Windows console compatibility
        icon = "[ALERT]" if result.is_alert_worthy else "[SCORE]"
        lines = [
            f"{icon} Insider Score: {result.score}/100+",
            "Reasons:" if result.reasons else "No flags triggered",
        ]
        for reason in result.reasons:
            lines.append(f"  - {reason}")
        return "\n".join(lines)
