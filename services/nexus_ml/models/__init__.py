"""ML model implementations for Nexus Trader.

All imports are guarded so missing optional packages (lightgbm, torch,
transformers, etc.) don't crash the entire service at startup.
"""
import importlib
import logging

_log = logging.getLogger(__name__)

_MODELS: dict[str, tuple[str, str]] = {
    "XGBoostDirectionModel": (".xgboost_direction", "XGBoostDirectionModel"),
    "LightGBMRegimeModel":   (".lightgbm_regime",   "LightGBMRegimeModel"),
    "HMMRegimeDetector":     (".hmm_regime",         "HMMRegimeDetector"),
    "LSTMSequenceModel":     (".lstm_sequence",      "LSTMSequenceModel"),
    "AnomalyDetector":       (".isolation_anomaly",  "AnomalyDetector"),
    "FinBERTSentiment":      (".finbert_sentiment",  "FinBERTSentiment"),
    "FeatureImportanceModel": (".random_forest_features", "FeatureImportanceModel"),
}

_loaded: dict[str, type] = {}

for _name, (_mod_path, _cls_name) in _MODELS.items():
    try:
        _mod = importlib.import_module(_mod_path, package=__name__)
        _loaded[_name] = getattr(_mod, _cls_name)
    except (ImportError, AttributeError) as exc:
        _log.warning("Skipping model %s: %s", _name, exc)


def __getattr__(name: str):
    if name in _loaded:
        return _loaded[name]
    if name in _MODELS:
        raise ImportError(
            f"Model {name} is not available (missing dependency). "
            f"Install the required package to enable it."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_MODELS.keys())
