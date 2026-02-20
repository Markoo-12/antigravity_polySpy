"""
Train the Momentum Ignition Filter (XGBoost).

Reads trades from the database, applies heuristic labeling, extracts
features, and trains a binary classifier to distinguish Toxic (momentum
ignition / manipulative scalping) from Insider (informed accumulation).

Usage:
    python train_filter.py
"""
import asyncio
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone
from collections import defaultdict

from src.database.repository import TradeRepository, Trade
from src.ml.momentum_features import MomentumFeatureExtractor


# Configuration
DATABASE_PATH = os.path.join("data", "trades.db")
MODEL_OUTPUT_PATH = os.path.join("data", "momentum_model.json")

# Labeling thresholds
FLIP_WINDOW_MINUTES = 5  # Held < 5 min = potential toxic
MIN_TRADES_FOR_TRAINING = 20  # Minimum trades needed to train


async def load_and_label_trades(repository: TradeRepository):
    """
    Load all trades and apply heuristic labeling.
    
    Label 0 (Toxic): Wallet bought & sold same asset within 5 minutes
    Label 1 (Insider): All other trades (held position or no quick flip)
    """
    import aiosqlite
    
    print("[1/4] Loading trades from database...")
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM trades WHERE amount_usdc > 0 ORDER BY timestamp ASC"
        )
        rows = await cursor.fetchall()
    
    print(f"  Found {len(rows)} total trades")
    
    if len(rows) < MIN_TRADES_FOR_TRAINING:
        print(f"  [WARN] Need at least {MIN_TRADES_FOR_TRAINING} trades to train. Aborting.")
        return [], []
    
    # Group trades by wallet (owner or proxy) + asset
    wallet_asset_trades = defaultdict(list)
    all_trades = []
    
    for row in rows:
        trade = Trade(
            tx_hash=row["tx_hash"],
            block_number=row["block_number"],
            timestamp=datetime.fromisoformat(row["timestamp"]) if isinstance(row["timestamp"], str) else row["timestamp"],
            order_hash=row["order_hash"],
            proxy_address=row["proxy_address"],
            owner_address=row["owner_address"],
            proxy_type=row["proxy_type"],
            asset_id=row["asset_id"],
            side=row["side"],
            amount_usdc=row["amount_usdc"],
            price=row["price"],
            market_id=row["market_id"] if "market_id" in row.keys() else None,
            id=row["id"],
        )
        
        # Ensure timezone-aware
        if trade.timestamp.tzinfo is None:
            trade.timestamp = trade.timestamp.replace(tzinfo=timezone.utc)
        
        wallet_key = trade.owner_address or trade.proxy_address
        wallet_asset_trades[(wallet_key, trade.asset_id)].append(trade)
        all_trades.append(trade)
    
    # Labeling: find rapid flippers
    print("[2/4] Applying heuristic labels...")
    
    toxic_trade_ids = set()
    
    for (wallet, asset_id), trades in wallet_asset_trades.items():
        buys = [t for t in trades if t.side == "buy"]
        sells = [t for t in trades if t.side == "sell"]
        
        for buy in buys:
            for sell in sells:
                # Check if sell happened within FLIP_WINDOW_MINUTES of buy
                time_diff = (sell.timestamp - buy.timestamp).total_seconds() / 60.0
                if 0 < time_diff <= FLIP_WINDOW_MINUTES:
                    # Quick flip detected — mark both as toxic
                    toxic_trade_ids.add(buy.id)
                    toxic_trade_ids.add(sell.id)
    
    # Build labeled dataset
    labeled_trades = []
    labels = []
    
    for trade in all_trades:
        label = 0 if trade.id in toxic_trade_ids else 1
        labeled_trades.append(trade)
        labels.append(label)
    
    toxic_count = sum(1 for l in labels if l == 0)
    insider_count = sum(1 for l in labels if l == 1)
    print(f"  Labels: {toxic_count} Toxic (0), {insider_count} Insider (1)")
    
    return labeled_trades, labels


async def extract_features_batch(
    trades: list,
    labels: list,
    repository: TradeRepository,
) -> tuple:
    """Extract features for all labeled trades."""
    print("[3/4] Extracting features...")
    
    extractor = MomentumFeatureExtractor(repository)
    
    feature_matrix = []
    valid_labels = []
    
    total = len(trades)
    for i, (trade, label) in enumerate(zip(trades, labels)):
        if (i + 1) % 200 == 0 or i == 0:
            print(f"  Processing trade {i+1}/{total}...")
        
        try:
            owner = trade.owner_address or trade.proxy_address
            features = await extractor.extract(trade=trade, owner_address=owner)
            
            from src.ml.momentum_filter import FEATURE_NAMES
            feature_vector = [features.get(name, 0.0) for name in FEATURE_NAMES]
            feature_matrix.append(feature_vector)
            valid_labels.append(label)
        except Exception as e:
            # Skip trades that fail feature extraction
            continue
    
    print(f"  Extracted features for {len(feature_matrix)} trades")
    return np.array(feature_matrix), np.array(valid_labels)


async def train_model(X: np.ndarray, y: np.ndarray):
    """Train XGBoost classifier and save model."""
    print("[4/4] Training XGBoost model...")
    
    try:
        import xgboost as xgb
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import classification_report, accuracy_score
    except ImportError:
        print("\n[ERROR] Missing dependencies. Install them first:")
        print("  pip install xgboost scikit-learn")
        return
    
    # Handle class imbalance
    n_toxic = sum(1 for label in y if label == 0)
    n_insider = sum(1 for label in y if label == 1)
    
    if n_toxic == 0 or n_insider == 0:
        print("[WARN] Only one class present in data — cannot train a meaningful model.")
        print("  Need both Toxic (rapid flippers) and Insider (holders) examples.")
        print("  Run the sentinel longer to accumulate more diverse trade data.")
        return
    
    scale_weight = n_insider / n_toxic if n_toxic > 0 else 1.0
    print(f"  Class balance: {n_toxic} toxic / {n_insider} insider (scale_pos_weight={scale_weight:.2f})")
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y if n_toxic >= 2 else None
    )
    
    # Train
    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        scale_pos_weight=scale_weight,
        eval_metric="logloss",
        random_state=42,
        use_label_encoder=False,
    )
    
    model.fit(X_train, y_train, verbose=False)
    
    # Evaluate
    y_pred = model.predict(X_test)
    
    print("\n" + "=" * 60)
    print("CLASSIFICATION REPORT")
    print("=" * 60)
    
    target_names = ["Toxic (0)", "Insider (1)"]
    print(classification_report(y_test, y_pred, target_names=target_names, zero_division=0))
    print(f"Accuracy: {accuracy_score(y_test, y_pred):.4f}")
    
    # Feature importance
    from src.ml.momentum_filter import FEATURE_NAMES
    importances = model.feature_importances_
    sorted_idx = np.argsort(importances)[::-1]
    
    print("\n" + "=" * 60)
    print("FEATURE IMPORTANCE")
    print("=" * 60)
    for idx in sorted_idx:
        bar = "#" * int(importances[idx] * 40)
        print(f"  {FEATURE_NAMES[idx]:30s} {importances[idx]:.4f} {bar}")
    
    # Save model
    os.makedirs(os.path.dirname(MODEL_OUTPUT_PATH), exist_ok=True)
    model.save_model(MODEL_OUTPUT_PATH)
    print(f"\n[OK] Model saved to {MODEL_OUTPUT_PATH}")
    
    # Quick sanity check
    proba = model.predict_proba(X_test[:3])
    print(f"\nSample predictions (first 3 test trades):")
    for i, p in enumerate(proba):
        print(f"  Trade {i+1}: P(Toxic)={p[0]:.3f}, P(Insider)={p[1]:.3f} → {'TOXIC' if p[0] > 0.75 else 'CLEAN'}")


async def main():
    print("=" * 60)
    print("MOMENTUM IGNITION FILTER — TRAINING")
    print("=" * 60)
    print()
    
    if not os.path.exists(DATABASE_PATH):
        print(f"[ERROR] Database not found at {DATABASE_PATH}")
        return
    
    repository = TradeRepository(DATABASE_PATH)
    
    # Step 1-2: Load and label
    trades, labels = await load_and_label_trades(repository)
    if not trades:
        return
    
    # Step 3: Extract features
    X, y = await extract_features_batch(trades, labels, repository)
    if len(X) < MIN_TRADES_FOR_TRAINING:
        print(f"[WARN] Only {len(X)} valid samples. Need {MIN_TRADES_FOR_TRAINING}+.")
        return
    
    # Step 4: Train
    await train_model(X, y)
    
    print("\n" + "=" * 60)
    print("DONE — Run 'python main.py' to use the model in production")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
