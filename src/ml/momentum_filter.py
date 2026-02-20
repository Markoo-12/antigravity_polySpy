"""
Momentum Ignition Filter — XGBoost model loader and predictor.

Loads a pre-trained XGBoost model and classifies trades as
Toxic (momentum ignition) or Insider (informed accumulation).
"""
import os
import numpy as np
from typing import Dict, Tuple

# Feature order must match training
FEATURE_NAMES = [
    "volume_per_second",
    "price_delta_60s",
    "avg_hold_time_minutes",
    "buy_sell_ratio",
    "trade_count_60s",
    "amount_vs_avg",
    "is_round_number",
    "consecutive_same_side",
]

# Default model path
DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "momentum_model.json",
)

# Toxic probability threshold — above this, the trade is blocked
TOXIC_THRESHOLD = 0.75


class MomentumFilter:
    """
    XGBoost-based filter for momentum ignition detection.
    
    - Loads a trained model from disk.
    - If no model exists, operates in pass-through mode (never blocks).
    - Predicts the probability that a trade is a momentum trap.
    """
    
    def __init__(self, model_path: str = None):
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.model = None
        self._load_model()
    
    def _load_model(self):
        """Attempt to load the XGBoost model from disk."""
        if not os.path.exists(self.model_path):
            print(f"[ML] No model found at {self.model_path} — running in pass-through mode")
            return
        
        try:
            import xgboost as xgb
            self.model = xgb.XGBClassifier()
            self.model.load_model(self.model_path)
            print(f"[ML] Loaded momentum filter model from {self.model_path}")
        except Exception as e:
            print(f"[ML] Failed to load model: {e} — running in pass-through mode")
            self.model = None
    
    def predict(self, features: Dict[str, float]) -> Tuple[float, str]:
        """
        Predict whether a trade is a momentum trap.
        
        Args:
            features: Dictionary of feature_name → float from MomentumFeatureExtractor.
            
        Returns:
            (toxic_probability, label) where:
            - toxic_probability: 0.0–1.0 probability the trade is manipulative
            - label: "MOMENTUM_TRAP", "CLEAN", or "NO_MODEL"
        """
        if self.model is None:
            return (0.0, "NO_MODEL")
        
        try:
            # Build feature array in correct order
            feature_array = np.array(
                [[features.get(name, 0.0) for name in FEATURE_NAMES]]
            )
            
            # Get probability of class 0 (Toxic)
            proba = self.model.predict_proba(feature_array)[0]
            
            # proba[0] = P(Toxic), proba[1] = P(Insider)
            toxic_prob = float(proba[0])
            
            if toxic_prob > TOXIC_THRESHOLD:
                return (toxic_prob, "MOMENTUM_TRAP")
            else:
                return (toxic_prob, "CLEAN")
                
        except Exception as e:
            print(f"[ML] Prediction error: {e}")
            return (0.0, "ERROR")
    
    @property
    def is_loaded(self) -> bool:
        """Whether a trained model is loaded."""
        return self.model is not None
