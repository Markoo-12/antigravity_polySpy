"""
End-to-end test for the Momentum Ignition ML Filter.
Tests: feature extraction, model loading, prediction, and pass-through fallback.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone
from src.database.repository import Trade, TradeRepository
from src.ml.momentum_features import MomentumFeatureExtractor
from src.ml.momentum_filter import MomentumFilter, FEATURE_NAMES

DATABASE_PATH = os.path.join("data", "trades.db")
MODEL_PATH = os.path.join("data", "momentum_model.json")

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} — {detail}")


async def main():
    global passed, failed
    
    print("=" * 60)
    print("ML MOMENTUM FILTER — VERIFICATION TEST")
    print("=" * 60)
    
    # ── Test 1: Module imports ──
    print("\n[TEST 1] Module Imports")
    try:
        from src.ml import MomentumFeatureExtractor, MomentumFilter
        check("src.ml imports", True)
    except Exception as e:
        check("src.ml imports", False, str(e))
    
    # ── Test 2: Model file exists ──
    print("\n[TEST 2] Trained Model")
    model_exists = os.path.exists(MODEL_PATH)
    check("Model file exists", model_exists, f"Expected at {MODEL_PATH}")
    
    # ── Test 3: Model loads successfully ──
    print("\n[TEST 3] Model Loading")
    mf = MomentumFilter(model_path=MODEL_PATH)
    check("MomentumFilter initializes", True)
    check("Model is loaded", mf.is_loaded, "Model failed to load")
    
    # ── Test 4: Feature extraction ──
    print("\n[TEST 4] Feature Extraction")
    repo = TradeRepository(DATABASE_PATH)
    extractor = MomentumFeatureExtractor(repo)
    
    # Create a sample trade
    sample_trade = Trade(
        tx_hash="0xTEST",
        block_number=1,
        timestamp=datetime.now(timezone.utc),
        order_hash="0xORDER",
        proxy_address="0xSAMPLE",
        owner_address="0xOWNER",
        proxy_type="test",
        asset_id="TEST_ASSET",
        side="buy",
        amount_usdc=5000.0,
        price=0.65,
        market_id="test-market",
    )
    
    features = await extractor.extract(trade=sample_trade, owner_address="0xOWNER")
    check("Feature extraction returns dict", isinstance(features, dict))
    check("All 8 features present", len(features) == 8, f"Got {len(features)}: {list(features.keys())}")
    
    for fname in FEATURE_NAMES:
        check(f"Feature '{fname}' exists", fname in features, f"Missing from {list(features.keys())}")
    
    all_numeric = all(isinstance(v, (int, float)) for v in features.values())
    check("All feature values are numeric", all_numeric)
    
    print(f"\n  Feature values:")
    for k, v in features.items():
        print(f"    {k:30s} = {v:.4f}")
    
    # ── Test 5: Prediction ──
    print("\n[TEST 5] Model Prediction")
    toxic_prob, label = mf.predict(features)
    check("Prediction returns tuple", isinstance(toxic_prob, float) and isinstance(label, str))
    check("Toxic probability in [0, 1]", 0.0 <= toxic_prob <= 1.0, f"Got {toxic_prob}")
    check("Label is valid", label in ("MOMENTUM_TRAP", "CLEAN", "NO_MODEL", "ERROR"), f"Got '{label}'")
    print(f"  Result: toxic_prob={toxic_prob:.4f}, label={label}")
    
    # ── Test 6: Pass-through mode (no model) ──
    print("\n[TEST 6] Pass-Through Fallback")
    mf_fake = MomentumFilter(model_path="nonexistent_model.json")
    check("No-model initializes without error", True)
    check("No-model is_loaded is False", not mf_fake.is_loaded)
    
    prob2, label2 = mf_fake.predict(features)
    check("No-model returns 0.0 probability", prob2 == 0.0, f"Got {prob2}")
    check("No-model returns NO_MODEL label", label2 == "NO_MODEL", f"Got '{label2}'")
    
    # ── Test 7: Prediction on known patterns ──
    print("\n[TEST 7] Known Pattern Predictions")
    
    # Simulate a rapid flipper: high velocity, low hold time, many trades in 60s
    toxic_features = {
        "volume_per_second": 500.0,
        "price_delta_60s": 0.15,
        "avg_hold_time_minutes": 1.5,
        "buy_sell_ratio": 0.9,
        "trade_count_60s": 8.0,
        "amount_vs_avg": 5.0,
        "is_round_number": 1.0,
        "consecutive_same_side": 6.0,
    }
    
    # Simulate a quiet accumulator: low velocity, long hold, few trades
    clean_features = {
        "volume_per_second": 10.0,
        "price_delta_60s": 0.005,
        "avg_hold_time_minutes": 120.0,
        "buy_sell_ratio": 0.6,
        "trade_count_60s": 1.0,
        "amount_vs_avg": 1.2,
        "is_round_number": 0.0,
        "consecutive_same_side": 1.0,
    }
    
    tp_toxic, lbl_toxic = mf.predict(toxic_features)
    tp_clean, lbl_clean = mf.predict(clean_features)
    
    print(f"  Rapid flipper:       toxic_prob={tp_toxic:.4f}, label={lbl_toxic}")
    print(f"  Quiet accumulator:   toxic_prob={tp_clean:.4f}, label={lbl_clean}")
    check("Rapid flipper has higher toxic prob than quiet accumulator", tp_toxic > tp_clean, 
          f"flipper={tp_toxic:.4f} vs accumulator={tp_clean:.4f}")
    
    # ── Summary ──
    print("\n" + "=" * 60)
    total = passed + failed
    print(f"RESULTS: {passed}/{total} checks passed ({failed} failed)")
    if failed == 0:
        print("✅ ALL TESTS PASSED")
    else:
        print(f"⚠️  {failed} checks need attention")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
