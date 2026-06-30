import unittest

from app.services import text_features


class KeywordStopwordsTests(unittest.TestCase):
    def test_про_preposition_is_not_extracted_as_a_keyword(self) -> None:
        keywords = text_features.extract_keywords("Что они решили про встречу по проекту?")

        self.assertNotIn("про", keywords)
        self.assertEqual(keywords, ["решить", "встреча", "проект"])


if __name__ == "__main__":
    unittest.main()
