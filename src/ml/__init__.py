"""
ML module for Momentum Ignition detection.
Provides XGBoost-based filtering to distinguish manipulative scalpers from informed insiders.
"""
from .momentum_features import MomentumFeatureExtractor
from .momentum_filter import MomentumFilter

__all__ = ["MomentumFeatureExtractor", "MomentumFilter"]
