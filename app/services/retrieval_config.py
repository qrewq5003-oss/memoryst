"""Loads retrieval scoring weights from config/retrieval_weights.yaml.

Kept as a separate module (not inlined in retrieve_service.py) so the weights
file's version and path are easy to find, and so retrieve_service.py keeps
using the same module-level constant names it always has.
"""

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "retrieval_weights.yaml"

REQUIRED_WEIGHT_KEYS = [
    "keyword_weight",
    "entity_weight",
    "importance_weight",
    "recency_weight",
    "pinned_bonus",
    "both_match_bonus",
    "min_retrieval_score",
    "near_duplicate_token_overlap",
    "close_score_layer_tie_epsilon",
    "semantic_boost",
    "relationship_cue_weight",
    "episodic_specificity_bonus",
    "episodic_low_value_penalty",
    "combined_overlap_keyword_weight",
    "combined_overlap_entity_weight",
    "support_strong_threshold",
    "support_medium_threshold",
    "support_multiplier_strong",
    "support_multiplier_medium",
    "support_multiplier_weak",
]


def _load(path: Path = CONFIG_PATH) -> dict:
    with path.open() as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "version" not in data:
        raise ValueError(f"Invalid retrieval weights config at {path}: missing 'version'")

    weights = data.get("weights", {})
    missing = [key for key in REQUIRED_WEIGHT_KEYS if key not in weights]
    if missing:
        raise ValueError(f"Invalid retrieval weights config at {path}: missing keys {missing}")

    for required_dict in ("relationship_support_bonus_by_layer", "layer_selection_caps", "layer_tie_priority"):
        if required_dict not in data:
            raise ValueError(f"Invalid retrieval weights config at {path}: missing '{required_dict}'")

    return data


_raw = _load()

VERSION: int = _raw["version"]
_weights = _raw["weights"]

KEYWORD_WEIGHT: float = _weights["keyword_weight"]
ENTITY_WEIGHT: float = _weights["entity_weight"]
IMPORTANCE_WEIGHT: float = _weights["importance_weight"]
RECENCY_WEIGHT: float = _weights["recency_weight"]
PINNED_BONUS: float = _weights["pinned_bonus"]
BOTH_MATCH_BONUS: float = _weights["both_match_bonus"]
MIN_RETRIEVAL_SCORE: float = _weights["min_retrieval_score"]
NEAR_DUPLICATE_TOKEN_OVERLAP: float = _weights["near_duplicate_token_overlap"]
CLOSE_SCORE_LAYER_TIE_EPSILON: float = _weights["close_score_layer_tie_epsilon"]
SEMANTIC_BOOST: float = _weights["semantic_boost"]
RELATIONSHIP_CUE_WEIGHT: float = _weights["relationship_cue_weight"]
EPISODIC_SPECIFICITY_BONUS: float = _weights["episodic_specificity_bonus"]
EPISODIC_LOW_VALUE_PENALTY: float = _weights["episodic_low_value_penalty"]
COMBINED_OVERLAP_KEYWORD_WEIGHT: float = _weights["combined_overlap_keyword_weight"]
COMBINED_OVERLAP_ENTITY_WEIGHT: float = _weights["combined_overlap_entity_weight"]
SUPPORT_STRONG_THRESHOLD: float = _weights["support_strong_threshold"]
SUPPORT_MEDIUM_THRESHOLD: float = _weights["support_medium_threshold"]
SUPPORT_MULTIPLIER_STRONG: float = _weights["support_multiplier_strong"]
SUPPORT_MULTIPLIER_MEDIUM: float = _weights["support_multiplier_medium"]
SUPPORT_MULTIPLIER_WEAK: float = _weights["support_multiplier_weak"]

RELATIONSHIP_SUPPORT_BONUS_BY_LAYER: dict[str, float] = _raw["relationship_support_bonus_by_layer"]
LAYER_SELECTION_CAPS: dict[str, int] = _raw["layer_selection_caps"]
LAYER_TIE_PRIORITY: dict[str, int] = _raw["layer_tie_priority"]
