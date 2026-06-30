import tempfile
import unittest
from pathlib import Path

import yaml

from app.services import retrieval_config


class RetrievalWeightsConfigTests(unittest.TestCase):
    def test_default_config_loads_and_matches_module_constants(self) -> None:
        data = retrieval_config._load(retrieval_config.CONFIG_PATH)

        self.assertEqual(data["version"], retrieval_config.VERSION)
        self.assertEqual(data["weights"]["keyword_weight"], retrieval_config.KEYWORD_WEIGHT)
        self.assertEqual(
            data["layer_selection_caps"],
            retrieval_config.LAYER_SELECTION_CAPS,
        )

    def test_rejects_config_missing_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(yaml.safe_dump({"weights": {}}))

            with self.assertRaises(ValueError):
                retrieval_config._load(path)

    def test_rejects_config_missing_weight_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            incomplete_weights = {k: 0.1 for k in retrieval_config.REQUIRED_WEIGHT_KEYS[:-1]}
            path.write_text(
                yaml.safe_dump(
                    {
                        "version": 1,
                        "weights": incomplete_weights,
                        "relationship_support_bonus_by_layer": {},
                        "layer_selection_caps": {},
                        "layer_tie_priority": {},
                    }
                )
            )

            with self.assertRaises(ValueError):
                retrieval_config._load(path)

    def test_rejects_config_missing_layer_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            full_weights = {k: 0.1 for k in retrieval_config.REQUIRED_WEIGHT_KEYS}
            path.write_text(
                yaml.safe_dump(
                    {
                        "version": 1,
                        "weights": full_weights,
                        "relationship_support_bonus_by_layer": {},
                        "layer_selection_caps": {},
                        # layer_tie_priority intentionally omitted
                    }
                )
            )

            with self.assertRaises(ValueError):
                retrieval_config._load(path)


if __name__ == "__main__":
    unittest.main()
