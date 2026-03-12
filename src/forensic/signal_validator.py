"""
Signal Validator - Backtest Module for Precision/Recall Analysis.

Compares high-score insider alerts against actual price movements
to validate prediction accuracy and auto-tune thresholds.

Used for:
1. Calculating precision/recall of high-score signals
2. Identifying optimal FULL_ANALYSIS_THRESHOLD
3. Auto-tuning feature weights based on historical performance
"""
import aiosqlite
import aiohttp
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Tuple

from ..config import DATABASE_PATH, INSIDER_ALERT_THRESHOLD


# =============================================================================
# CONFIGURATION
# =============================================================================
SIGNAL_VALIDATION_LOOKBACK_DAYS = 30
PRICE_GAIN_THRESHOLD = 0.10  # 10% price gain = true positive
PRICE_DECLINE_THRESHOLD = -0.05  # 5% decline = successful short prediction
VALIDATION_WINDOW_HOURS = 24  # Check price 24h after trade

# Polymarket CLOB API
CLOB_API_BASE = "https://clob.polymarket.com"


@dataclass
class TradeSignal:
    """A trade signal to validate."""
    trade_id: int
    tx_hash: str
    asset_id: str
    owner_address: str
    trade_timestamp: datetime
    amount_usdc: float
    insider_score: int
    side: str  # 'buy' or 'sell'
    
    # Validation results (filled after checking)
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    price_change_pct: Optional[float] = None
    is_true_positive: Optional[bool] = None


@dataclass
class ValidationResult:
    """Result of signal validation analysis."""
    precision: float  # True positives / Total predicted positives
    recall: float  # True positives / All actual positives
    accuracy: float  # Correct predictions / Total predictions
    f1_score: float  # Harmonic mean of precision and recall
    
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    
    tested_trades: int
    avg_price_change: float
    optimal_threshold: int
    
    threshold_analysis: Dict[int, Dict[str, float]] = field(default_factory=dict)
    signals: List[TradeSignal] = field(default_factory=list)


async def validate_signals(
    lookback_days: int = SIGNAL_VALIDATION_LOOKBACK_DAYS,
    score_threshold: int = INSIDER_ALERT_THRESHOLD,
    db_path: str = DATABASE_PATH,
) -> ValidationResult:
    """
    Validate high-score insider signals against actual price movements.
    
    Logic:
    1. Query trades with insider_score >= threshold from past N days
    2. For each trade, fetch price at trade time and 24h later
    3. If price moved favorably (≥10% for buys, ≤-5% for sells), mark as true positive
    4. Calculate precision/recall metrics
    
    Args:
        lookback_days: Number of days to look back for trades
        score_threshold: Minimum insider score to consider
        db_path: Path to trades.db
        
    Returns:
        ValidationResult with precision, recall, and optimal threshold
    """
    signals: List[TradeSignal] = []
    
    # Calculate cutoff date
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    
    # Query high-score trades from database
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            
            cursor = await db.execute(
                """
                SELECT id, tx_hash, asset_id, owner_address, timestamp, 
                       amount_usdc, insider_score, side
                FROM trades
                WHERE insider_score >= ?
                  AND timestamp >= ?
                  AND timestamp <= ?
                ORDER BY insider_score DESC
                """,
                (
                    score_threshold,
                    cutoff.isoformat(),
                    # Only trades old enough to have 24h outcome
                    (datetime.now(timezone.utc) - timedelta(hours=VALIDATION_WINDOW_HOURS)).isoformat(),
                ),
            )
            
            rows = await cursor.fetchall()
            
            for row in rows:
                signals.append(TradeSignal(
                    trade_id=row["id"],
                    tx_hash=row["tx_hash"],
                    asset_id=row["asset_id"],
                    owner_address=row["owner_address"],
                    trade_timestamp=datetime.fromisoformat(row["timestamp"]),
                    amount_usdc=row["amount_usdc"],
                    insider_score=row["insider_score"],
                    side=row["side"],
                ))
    
    except Exception as e:
        print(f"[VALIDATOR] Database error: {e}")
        return ValidationResult(
            precision=0.0, recall=0.0, accuracy=0.0, f1_score=0.0,
            true_positives=0, false_positives=0,
            true_negatives=0, false_negatives=0,
            tested_trades=0, avg_price_change=0.0,
            optimal_threshold=score_threshold,
        )
    
    if not signals:
        print(f"[VALIDATOR] No high-score trades found in last {lookback_days} days")
        return ValidationResult(
            precision=0.0, recall=0.0, accuracy=0.0, f1_score=0.0,
            true_positives=0, false_positives=0,
            true_negatives=0, false_negatives=0,
            tested_trades=0, avg_price_change=0.0,
            optimal_threshold=score_threshold,
        )
    
    # Validate each signal by checking price movement
    validated_signals = []
    for signal in signals:
        try:
            validated = await _validate_single_signal(signal)
            if validated.is_true_positive is not None:
                validated_signals.append(validated)
        except Exception as e:
            print(f"[VALIDATOR] Error validating {signal.tx_hash}: {e}")
    
    # Calculate metrics
    true_positives = sum(1 for s in validated_signals if s.is_true_positive)
    false_positives = sum(1 for s in validated_signals if not s.is_true_positive)
    
    # For simplicity, we assume all high-score signals are "predicted positives"
    total_predicted = len(validated_signals)
    
    precision = true_positives / total_predicted if total_predicted > 0 else 0.0
    
    # Approximate recall: true positives / total trades in the same time window
    total_trades_in_window = 0
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM trades WHERE timestamp >= ? AND timestamp <= ?",
                (
                    cutoff.isoformat(),
                    (datetime.now(timezone.utc) - timedelta(hours=VALIDATION_WINDOW_HOURS)).isoformat(),
                ),
            )
            row = await cursor.fetchone()
            total_trades_in_window = row[0] if row else 0
    except Exception as e:
        print(f"[VALIDATOR] Error querying total trades for recall: {e}")
        total_trades_in_window = 0
    recall = true_positives / max(total_trades_in_window, 1)
    
    # F1 Score
    f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    
    # Average price change
    price_changes = [s.price_change_pct for s in validated_signals if s.price_change_pct is not None]
    avg_price_change = float(np.mean(price_changes)) if price_changes else 0.0
    
    # Calculate optimal threshold by testing different thresholds
    threshold_analysis = await _analyze_thresholds(signals, validated_signals)
    optimal_threshold = _find_optimal_threshold(threshold_analysis)
    
    return ValidationResult(
        precision=precision,
        recall=recall,
        accuracy=precision,  # Simplified
        f1_score=f1_score,
        true_positives=true_positives,
        false_positives=false_positives,
        true_negatives=0,  # Not tracked
        false_negatives=0,  # Not tracked
        tested_trades=len(validated_signals),
        avg_price_change=avg_price_change,
        optimal_threshold=optimal_threshold,
        threshold_analysis=threshold_analysis,
        signals=validated_signals,
    )


async def _validate_single_signal(signal: TradeSignal) -> TradeSignal:
    """
    Validate a single trade signal by checking price movement.
    
    Fetches price at trade time and 24h later from Polymarket CLOB API.
    """
    # Get price at trade time and 24h later
    entry_ts = int(signal.trade_timestamp.timestamp())
    exit_ts = entry_ts + (VALIDATION_WINDOW_HOURS * 3600)
    
    # Fetch price history from CLOB API
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{CLOB_API_BASE}/prices-history"
            params = {
                "market": signal.asset_id,
                "startTs": entry_ts - 3600,  # 1h before entry
                "endTs": exit_ts + 3600,  # 1h after exit
                "fidelity": 60,  # Hourly
            }
            
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    signal.is_true_positive = None
                    return signal
                
                data = await resp.json()
                prices = data.get("history", [])
                
                if len(prices) < 2:
                    signal.is_true_positive = None
                    return signal
                
                # Find entry and exit prices (closest to timestamps)
                entry_price = _find_price_at_timestamp(prices, entry_ts)
                exit_price = _find_price_at_timestamp(prices, exit_ts)
                
                if entry_price is None or exit_price is None:
                    signal.is_true_positive = None
                    return signal
                
                signal.entry_price = entry_price
                signal.exit_price = exit_price
                
                # Calculate price change
                price_change = (exit_price - entry_price) / entry_price if entry_price > 0 else 0
                signal.price_change_pct = price_change
                
                # Determine if true positive based on trade side
                if signal.side == "buy":
                    # Buy signal is true positive if price went up
                    signal.is_true_positive = price_change >= PRICE_GAIN_THRESHOLD
                else:
                    # Sell signal is true positive if price went down
                    signal.is_true_positive = price_change <= PRICE_DECLINE_THRESHOLD
    
    except Exception as e:
        print(f"[VALIDATOR] Price fetch error for {signal.asset_id}: {e}")
        signal.is_true_positive = None
    
    return signal


def _find_price_at_timestamp(prices: List[dict], target_ts: int) -> Optional[float]:
    """Find the price closest to target timestamp."""
    if not prices:
        return None
    
    closest_price = None
    min_diff = float('inf')
    
    for p in prices:
        ts = p.get("t", 0)
        diff = abs(ts - target_ts)
        if diff < min_diff:
            min_diff = diff
            closest_price = p.get("p", 0)
    
    return closest_price


async def _analyze_thresholds(
    all_signals: List[TradeSignal],
    validated_signals: List[TradeSignal],
) -> Dict[int, Dict[str, float]]:
    """
    Analyze precision/recall at different score thresholds.
    """
    thresholds = [50, 60, 70, 80, 90, 100, 110, 120]
    analysis = {}
    
    for threshold in thresholds:
        filtered = [s for s in validated_signals if s.insider_score >= threshold]
        tp = sum(1 for s in filtered if s.is_true_positive)
        total = len(filtered)
        
        precision = tp / total if total > 0 else 0.0
        
        analysis[threshold] = {
            "precision": precision,
            "count": total,
            "true_positives": tp,
        }
    
    return analysis


def _find_optimal_threshold(analysis: Dict[int, Dict[str, float]]) -> int:
    """
    Find the optimal threshold that balances precision and coverage.
    
    Prefers higher precision while maintaining reasonable coverage.
    """
    best_threshold = 70  # Default
    best_score = 0.0
    
    for threshold, metrics in analysis.items():
        precision = metrics.get("precision", 0)
        count = metrics.get("count", 0)
        
        # Score = precision * log(count + 1) to balance precision with coverage
        if count > 0:
            score = precision * np.log(count + 1)
            if score > best_score:
                best_score = score
                best_threshold = threshold
    
    return best_threshold


def print_validation_report(result: ValidationResult) -> None:
    """Print a human-readable validation report."""
    print("\n" + "=" * 60)
    print("SIGNAL VALIDATION REPORT")
    print("=" * 60)
    print(f"Trades Tested: {result.tested_trades}")
    print(f"Precision: {result.precision:.1%}")
    print(f"True Positives: {result.true_positives}")
    print(f"False Positives: {result.false_positives}")
    print(f"Avg Price Change: {result.avg_price_change:.1%}")
    print(f"Optimal Threshold: {result.optimal_threshold}")
    print("\nThreshold Analysis:")
    for threshold, metrics in sorted(result.threshold_analysis.items()):
        print(f"  Score >= {threshold}: {metrics['precision']:.1%} precision ({metrics['count']} trades)")
    print("=" * 60)
