import shutil
import tempfile
import unittest

from app.services import vector_store as vs


class BuildChromaWhereTests(unittest.TestCase):
    def test_single_filter_passed_through_unchanged(self) -> None:
        self.assertEqual(vs._build_chroma_where({"chat_id": "chat-1"}), {"chat_id": "chat-1"})

    def test_empty_filter_passed_through_unchanged(self) -> None:
        self.assertEqual(vs._build_chroma_where({}), {})

    def test_multiple_filters_combined_with_and(self) -> None:
        result = vs._build_chroma_where({"chat_id": "chat-1", "character_id": "char-1"})

        self.assertEqual(
            result,
            {"$and": [{"chat_id": "chat-1"}, {"character_id": "char-1"}]},
        )


class ChromaQueryCombinedFilterTests(unittest.TestCase):
    """Regression test for the original bug: chromadb rejects a `where` dict
    with more than one top-level key unless wrapped in `$and`. This exercises
    the real chromadb client (not mocked) the same way query_similar(chat_id=,
    character_id=) does, without going through embed_text/add_memory so it
    needs no Google API key."""

    def setUp(self) -> None:
        if not vs.HAS_CHROMADB:
            self.skipTest("chromadb not installed")

        self.tmp_dir = tempfile.mkdtemp()
        self.original_path = vs.config.CHROMADB_PATH
        self.original_client = vs._client
        self.original_collection = vs._collection
        vs.config.CHROMADB_PATH = self.tmp_dir
        vs._client = None
        vs._collection = None
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        vs.config.CHROMADB_PATH = self.original_path
        vs._client = self.original_client
        vs._collection = self.original_collection
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_query_with_chat_id_and_character_id_does_not_raise(self) -> None:
        vec_a = [0.1] * 8
        vec_b = [0.9] * 8
        vs._chroma_add("mem-a", vec_a, {"chat_id": "chat-1", "character_id": "char-1"})
        vs._chroma_add("mem-b", vec_b, {"chat_id": "chat-2", "character_id": "char-2"})

        results = vs._chroma_query(vec_a, 10, {"chat_id": "chat-1", "character_id": "char-1"})

        self.assertEqual([r["id"] for r in results], ["mem-a"])

    def test_query_with_mismatched_combined_filter_excludes_partial_match(self) -> None:
        vec_a = [0.1] * 8
        vs._chroma_add("mem-a", vec_a, {"chat_id": "chat-1", "character_id": "char-1"})

        results = vs._chroma_query(vec_a, 10, {"chat_id": "chat-1", "character_id": "char-2"})

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
