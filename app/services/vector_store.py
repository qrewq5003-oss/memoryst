import json
import math
import sys
import threading
from array import array
from pathlib import Path

import logging

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


logger = logging.getLogger(__name__)

NANOGPT_EMBED_URL = "https://nano-gpt.com/api/v1/embeddings"
COHERE_EMBED_URL = "https://api.cohere.ai/v2/embed"


def _call_embed_openai_shaped(text: str | list[str]) -> list[list[float]]:
    """OpenAI-shaped /v1/embeddings, which is what nano-gpt serves.

    Its batch path actually works, unlike the Google one below: a list of 50 texts is
    one request. That matters beyond tidiness - the Google batch has always been broken
    (see _call_embed), so every backfill ran one call per memory, which is why only 585
    of 4660 memories ever got a vector.
    """
    batch = text if isinstance(text, list) else [text]
    key = (config.LLM_API_KEY or "").split(",")[0].strip()
    if not key:
        raise RuntimeError("LLM_API_KEY is required for the nanogpt embedding provider")
    resp = httpx.post(
        NANOGPT_EMBED_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": config.active_embedding_model(), "input": batch},
        timeout=180,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Embedding API error {resp.status_code}: {resp.text[:300]}")
    data = sorted(resp.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def _call_embed_cohere(text: str | list[str], is_query: bool = False) -> list[list[float]]:
    """Cohere v2, the one provider that embeds a query differently from a document.

    `input_type` is the whole reason it is here; without it this is just another
    symmetric embedder. Note the monthly request ceiling on the trial tier - this is a
    measurement tool here, not the default provider.
    """
    batch = text if isinstance(text, list) else [text]
    if not config.COHERE_API_KEY:
        raise RuntimeError("COHERE_API_KEY is required for the cohere embedding provider")
    resp = httpx.post(
        COHERE_EMBED_URL,
        headers={"Authorization": f"Bearer {config.COHERE_API_KEY}"},
        json={
            "texts": batch,
            "model": config.active_embedding_model(),
            "embedding_types": ["float"],
            "input_type": "search_query" if is_query else "search_document",
            "truncate": "END",
        },
        timeout=180,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Embedding API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["embeddings"]["float"]


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
                config.active_embedding_model(),
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
        # Filtering by model, not only by dimensionality. Two models can agree on the
        # number of dimensions and still share no vector space at all - switching
        # gemini-embedding-2-preview for gemini-embedding-001 keeps 768 on both sides,
        # so the dimension guard below would wave every stale row through and compare
        # it, returning confident numbers that mean nothing. The column was already
        # written on every insert and read by nothing.
        params: list[object] = [config.active_embedding_model()]
        clauses = ["model = ?"]
        for column in ("chat_id", "character_id"):
            if where and where.get(column):
                clauses.append(f"{column} = ?")
                params.append(str(where[column]))
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
    """Whether embedding is possible at all - per provider, not per Google key.

    This used to ask only about GOOGLE_API_KEYS, which silently reported the layer as
    off for every other provider.
    """
    provider = (config.EMBEDDING_PROVIDER or "google").lower()
    if provider == "nanogpt":
        return bool((config.LLM_API_KEY or "").strip())
    if provider == "cohere":
        return bool(config.COHERE_API_KEY)
    _ensure_keys()
    return bool(_keys)


def add_memories_batch(items: list[tuple[str, str, dict]]) -> int:
    """Embed and store many memories in one request where the provider allows it.

    One call per memory is what kept coverage at 585 of 4660: the Google batch path has
    never worked (it posts to the single-embed endpoint), so a backfill of this database
    meant 4660 sequential round trips, each with a pacing sleep. The nano-gpt path takes
    the whole list.

    Returns the number stored. Best-effort per the same reasoning as add_memory: a
    missing vector costs a memory its semantic boost and nothing else.
    """
    if not items:
        return 0
    try:
        vectors = embed_batch([content for _, content, _ in items])
    except RuntimeError as error:
        # bge-m3 caps a *request*, not a text: 50 short facts fit and 50 long scene
        # excerpts do not. Measured 2026-09-21, one batch of 50 in 95 blew the limit and
        # the whole batch was lost. Halving is enough because the limit is on the sum -
        # and a single item over the cap still ends up alone, where the error is real
        # and belongs to that memory rather than to its 49 neighbours.
        if "context_length_exceeded" not in str(error) and "too large" not in str(error):
            raise
        if len(items) == 1:
            logger.warning("memory %s is too long to embed on its own", items[0][0])
            return 0
        half = len(items) // 2
        return add_memories_batch(items[:half]) + add_memories_batch(items[half:])
    stored = 0
    for (memory_id, _content, metadata), vector in zip(items, vectors):
        try:
            _sqlite_add(memory_id, vector, metadata)
            stored += 1
        except Exception:
            logger.exception("failed to store embedding for %s", memory_id)
    return stored


def get_active_key_index() -> int:
    _ensure_keys()
    return _key_index


def get_key_count() -> int:
    _ensure_keys()
    return len(_keys)


def _dispatch_embed(text: str | list[str], is_query: bool = False) -> list[list[float]]:
    provider = (config.EMBEDDING_PROVIDER or "google").lower()
    if provider == "nanogpt":
        return _call_embed_openai_shaped(text)
    if provider == "cohere":
        return _call_embed_cohere(text, is_query=is_query)
    return _call_embed(text)


def embed_text(text: str, is_query: bool = False) -> list[float]:
    return _dispatch_embed(text, is_query=is_query)[0]


def embed_batch(texts: list[str], is_query: bool = False) -> list[list[float]]:
    return _dispatch_embed(texts, is_query=is_query)


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


# How long a bulk caller should wait between embeddings.
#
# The library itself never sleeps: a live /memory/store embeds one or two facts a turn
# and pacing it would only slow a request the user is waiting on. Bursts come from the
# scripts - a re-extraction writes 3-5 facts per scene back to back, and on 2026-09-21
# that tripped Google's per-minute limit and returned 429 RESOURCE_EXHAUSTED. The quota
# came back within minutes, which is what a per-minute limit looks like; a daily one
# would have held until midnight Pacific.
#
# 0.6s is what scripts/embed_memories.py had been using without ever hitting the limit,
# so it is a measured value rather than a guess - roughly 100 calls a minute.
BULK_EMBED_DELAY_SECONDS = 0.6


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
