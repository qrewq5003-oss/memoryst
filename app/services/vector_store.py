import json
import math
import sys
import threading
from array import array
from pathlib import Path

import httpx

from app.config import config
from app.db import get_connection

try:
    import chromadb

    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False

_client = None
_collection = None

_key_lock = threading.Lock()
_keys: list[str] = []
_key_index: int = 0

KEYS_FILE = Path(config.CHROMADB_PATH).parent / "google_keys.json"
EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"



def _load_keys() -> list[str]:
    keys = list(config.GOOGLE_API_KEYS)
    if KEYS_FILE.exists():
        try:
            file_keys = json.loads(KEYS_FILE.read_text())
            if isinstance(file_keys, list) and file_keys:
                keys = file_keys
        except (json.JSONDecodeError, OSError):
            pass
    return keys


def _save_keys() -> None:
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEYS_FILE.write_text(json.dumps(_keys, indent=2))


def _ensure_keys() -> None:
    global _keys
    if not _keys:
        _keys = _load_keys()


def _get_active_key() -> str:
    _ensure_keys()
    return _keys[_key_index]


def _rotate_key() -> bool:
    global _key_index
    with _key_lock:
        if len(_keys) <= 1:
            return False
        _key_index = (_key_index + 1) % len(_keys)
        return True


def _call_embed(text: str | list[str]) -> list[list[float]]:
    _ensure_keys()
    if not _keys:
        raise RuntimeError("No Google API keys configured")

    url = EMBED_URL.format(model=config.GOOGLE_EMBEDDING_MODEL)

    for attempt in range(len(_keys)):
        key = _get_active_key()
        api_url = f"{url}?key={key}"

        if isinstance(text, list):
            payload = {
                "requests": [
                    {
                        "model": f"models/{config.GOOGLE_EMBEDDING_MODEL}",
                        "content": {"parts": [{"text": t}]},
                        "outputDimensionality": config.GOOGLE_EMBEDDING_DIM,
                    }
                    for t in text
                ]
            }
        else:
            payload = {
                "model": f"models/{config.GOOGLE_EMBEDDING_MODEL}",
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": config.GOOGLE_EMBEDDING_DIM,
            }

        resp = httpx.post(api_url, json=payload, timeout=30)

        if resp.status_code == 200:
            data = resp.json()
            if isinstance(text, list):
                return [r["embedding"]["values"] for r in data["embeddings"]]
            return [data["embedding"]["values"]]

        if resp.status_code == 429:
            if attempt < len(_keys) - 1 and _rotate_key():
                continue
            raise RuntimeError(f"Rate limited on all keys: {resp.text}")

        raise RuntimeError(f"Embedding API error {resp.status_code}: {resp.text}")

    raise RuntimeError("All API keys exhausted")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# --- ChromaDB backend ---

def _chroma_get_collection():
    global _client, _collection
    if _client is None:
        _client = chromadb.PersistentClient(path=config.CHROMADB_PATH)
    if _collection is None:
        _collection = _client.get_or_create_collection(name="memories", metadata={"hnsw:space": "cosine"})
    return _collection


def _chroma_add(memory_id: str, embedding: list[float], metadata: dict) -> None:
    _chroma_get_collection().upsert(ids=[memory_id], embeddings=[embedding], metadatas=[metadata])


def _build_chroma_where(where: dict) -> dict:
    """Chroma's `where` only accepts one operator at the top level — multiple
    equality filters must be combined explicitly via `$and`."""
    if len(where) <= 1:
        return where
    return {"$and": [{key: value} for key, value in where.items()]}


# The Chroma path returns only its own top-N, so there is no scanned distribution to
# report and `scanned_median_similarity` stays absent - the caller falls back to the
# absolute floor alone. chromadb is not installed on the machine this runs on, so the
# JSON path above is the one in use; this is kept correct rather than left to rot.
def _chroma_query(embedding: list[float], n_results: int, where: dict | None) -> list[dict]:
    col = _chroma_get_collection()
    count = col.count()
    if count <= 0:
        return []
    kwargs: dict = {"query_embeddings": [embedding], "n_results": min(n_results, count)}
    if where:
        kwargs["where"] = _build_chroma_where(where)
    results = col.query(**kwargs)
    items = []
    for i in range(len(results["ids"][0])):
        distance = results["distances"][0][i]
        items.append(
            {
                "id": results["ids"][0][i],
                "similarity": 1.0 - distance,
                "distance": distance,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            }
        )
    return items


def _chroma_delete(memory_id: str) -> None:
    try:
        _chroma_get_collection().delete(ids=[memory_id])
    except Exception:
        pass


def _chroma_count() -> int:
    return _chroma_get_collection().count()


# --- SQLite backend (fallback when chromadb is absent) ---
#
# This replaced a JSON file, which could not survive this database. `_json_add` rewrote
# the whole store on every insert, so backfilling the 4639 memories in data/memory.db at
# 3072 dimensions would have written ~661 GB to the phone's flash to produce a 285 MB
# file, then parsed all of it into Python floats on every query - on the retrieve path,
# which blocks generation.
#
# Vectors live as float32 blobs beside the memories they belong to: 4639 x 768 x 4 bytes
# is 14 MB, an insert is one row, and a query reads only the rows for one chat. The
# dimension drop is the API's own `outputDimensionality`, which returns an already
# normalised vector - verified 2026-09-20, norm 1.0 at 768.


def _vector_to_blob(embedding: list[float]) -> bytes:
    return array("f", embedding).tobytes()


def _blob_to_vector(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return values.tolist()


def _sqlite_add(memory_id: str, embedding: list[float], metadata: dict) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO memory_embeddings
                (memory_id, chat_id, character_id, dimensions, model, vector)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                character_id = excluded.character_id,
                dimensions = excluded.dimensions,
                model = excluded.model,
                vector = excluded.vector
            """,
            (
                memory_id,
                str(metadata.get("chat_id") or ""),
                str(metadata.get("character_id") or ""),
                len(embedding),
                config.GOOGLE_EMBEDDING_MODEL,
                _vector_to_blob(embedding),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _sqlite_query(embedding: list[float], n_results: int, where: dict | None) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT memory_id, chat_id, character_id, dimensions, vector FROM memory_embeddings"
        params: list[object] = []
        clauses = []
        for column in ("chat_id", "character_id"):
            if where and where.get(column):
                clauses.append(f"{column} = ?")
                params.append(str(where[column]))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    scored = []
    for row in rows:
        stored = _blob_to_vector(row["vector"])
        # A row embedded at a different dimensionality (an older backfill, a model change)
        # cannot be compared with this query's vector at all. Skipping it is right;
        # comparing the overlapping prefix would silently return nonsense similarities.
        if len(stored) != len(embedding):
            continue
        similarity = _cosine_similarity(embedding, stored)
        scored.append(
            {
                "id": row["memory_id"],
                "similarity": similarity,
                "distance": 1.0 - similarity,
                "metadata": {"chat_id": row["chat_id"], "character_id": row["character_id"]},
            }
        )

    scored.sort(key=lambda item: item["distance"])
    if scored:
        similarities = sorted(item["similarity"] for item in scored)
        median = similarities[len(similarities) // 2]
        for item in scored:
            item["scanned_median_similarity"] = median
            item["scanned_count"] = len(similarities)
    return scored[:n_results]


def _sqlite_delete(memory_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
        conn.commit()
    finally:
        conn.close()


def _sqlite_count() -> int:
    conn = get_connection()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0])
    finally:
        conn.close()


def _use_chroma() -> bool:
    return HAS_CHROMADB


def is_vector_store_enabled() -> bool:
    _ensure_keys()
    return bool(_keys)


def get_active_key_index() -> int:
    _ensure_keys()
    return _key_index


def get_key_count() -> int:
    _ensure_keys()
    return len(_keys)


def embed_text(text: str) -> list[float]:
    return _call_embed(text)[0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    return _call_embed(texts)


def add_key(key: str) -> None:
    _ensure_keys()
    with _key_lock:
        if key not in _keys:
            _keys.append(key)
            _save_keys()


def remove_key(key: str) -> bool:
    global _key_index
    _ensure_keys()
    with _key_lock:
        if key in _keys and len(_keys) > 1:
            _keys.remove(key)
            if _key_index >= len(_keys):
                _key_index = 0
            _save_keys()
            return True
        return False


def list_keys() -> list[dict[str, str]]:
    _ensure_keys()
    with _key_lock:
        result = []
        for i, k in enumerate(_keys):
            masked = f"{k[:8]}...{k[-4:]}" if len(k) > 12 else "***"
            result.append({"masked": masked, "active": i == _key_index})
        return result


def add_memory(memory_id: str, content: str, metadata: dict | None = None) -> None:
    """Embed one memory. Best-effort: a failure here must not fail the write it follows.

    store_service calls this immediately after create_memory, inside the loop over a
    scene's facts, and called it unguarded. So the first time the embedding provider
    answered 429 - Google's quota, exhausted mid-run on 2026-09-21 - the exception came
    back out through /memory/store: the client saw a failed store, the facts already
    written stayed written, and the rest of the scene was never stored at all. The
    embedding is an enhancement to a memory, not part of storing it, and it was the only
    thing in that path able to take the whole request down.

    A memory without a vector is still fully retrievable: retrieval is lexical first and
    the semantic layer only adds a graded boost on top.
    """
    if not is_vector_store_enabled():
        return
    try:
        embedding = embed_text(content)
        meta = metadata or {}
        if _use_chroma():
            _chroma_add(memory_id, embedding, meta)
        else:
            _sqlite_add(memory_id, embedding, meta)
    except Exception as exc:
        print(
            f"[vector_store] could not embed memory {memory_id}: {exc}",
            file=sys.stderr,
            flush=True,
        )


def query_similar(text: str, *, n_results: int = 10, chat_id: str | None = None, character_id: str | None = None) -> list[dict]:
    """Semantic candidates for a query. Best-effort, for the same reason as add_memory:
    this runs inside /memory/retrieve, which blocks generation, and an exhausted
    embedding quota must cost the turn its semantic boost rather than its memory."""
    if not is_vector_store_enabled():
        return []
    try:
        embedding = embed_text(text)
    except Exception as exc:
        print(f"[vector_store] could not embed the query: {exc}", file=sys.stderr, flush=True)
        return []
    where = {}
    if chat_id:
        where["chat_id"] = chat_id
    if character_id:
        where["character_id"] = character_id
    if _use_chroma():
        return _chroma_query(embedding, n_results, where or None)
    return _sqlite_query(embedding, n_results, where or None)


def delete_memory(memory_id: str) -> None:
    if not is_vector_store_enabled():
        return
    if _use_chroma():
        _chroma_delete(memory_id)
    else:
        _sqlite_delete(memory_id)


def get_collection_count() -> int:
    if not is_vector_store_enabled():
        return 0
    if _use_chroma():
        return _chroma_count()
    return _sqlite_count()
