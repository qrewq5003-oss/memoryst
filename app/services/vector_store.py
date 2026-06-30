import json
import math
import threading
from pathlib import Path

import httpx

from app.config import config

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
VECTORS_FILE = Path(config.CHROMADB_PATH).parent / "vectors.json"
EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"

_vectors_db: dict[str, dict] = {}


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
            payload = {"requests": [{"model": f"models/{config.GOOGLE_EMBEDDING_MODEL}", "content": {"parts": [{"text": t}]}} for t in text]}
        else:
            payload = {"model": f"models/{config.GOOGLE_EMBEDDING_MODEL}", "content": {"parts": [{"text": text}]}}

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


def _chroma_query(embedding: list[float], n_results: int, where: dict | None) -> list[dict]:
    col = _chroma_get_collection()
    count = col.count()
    if count <= 0:
        return []
    kwargs: dict = {"query_embeddings": [embedding], "n_results": min(n_results, count)}
    if where:
        kwargs["where"] = where
    results = col.query(**kwargs)
    items = []
    for i in range(len(results["ids"][0])):
        items.append({"id": results["ids"][0][i], "distance": results["distances"][0][i], "metadata": results["metadatas"][0][i] if results["metadatas"] else {}})
    return items


def _chroma_delete(memory_id: str) -> None:
    try:
        _chroma_get_collection().delete(ids=[memory_id])
    except Exception:
        pass


def _chroma_count() -> int:
    return _chroma_get_collection().count()


# --- JSON file backend (fallback) ---

def _json_load() -> None:
    global _vectors_db
    if _vectors_db:
        return
    if VECTORS_FILE.exists():
        try:
            _vectors_db = json.loads(VECTORS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            _vectors_db = {}


def _json_save() -> None:
    VECTORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    VECTORS_FILE.write_text(json.dumps(_vectors_db))


def _json_add(memory_id: str, embedding: list[float], metadata: dict) -> None:
    _json_load()
    _vectors_db[memory_id] = {"embedding": embedding, "metadata": metadata}
    _json_save()


def _json_query(embedding: list[float], n_results: int, where: dict | None) -> list[dict]:
    _json_load()
    scored = []
    for mid, entry in _vectors_db.items():
        if where:
            match = all(entry["metadata"].get(k) == v for k, v in where.items())
            if not match:
                continue
        sim = _cosine_similarity(embedding, entry["embedding"])
        scored.append({"id": mid, "distance": 1.0 - sim, "metadata": entry["metadata"]})
    scored.sort(key=lambda x: x["distance"])
    return scored[:n_results]


def _json_delete(memory_id: str) -> None:
    _json_load()
    _vectors_db.pop(memory_id, None)
    _json_save()


def _json_count() -> int:
    _json_load()
    return len(_vectors_db)


# --- Dispatch ---

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
    if not is_vector_store_enabled():
        return
    embedding = embed_text(content)
    meta = metadata or {}
    if _use_chroma():
        _chroma_add(memory_id, embedding, meta)
    else:
        _json_add(memory_id, embedding, meta)


def query_similar(text: str, *, n_results: int = 10, chat_id: str | None = None, character_id: str | None = None) -> list[dict]:
    if not is_vector_store_enabled():
        return []
    embedding = embed_text(text)
    where = {}
    if chat_id:
        where["chat_id"] = chat_id
    if character_id:
        where["character_id"] = character_id
    if _use_chroma():
        return _chroma_query(embedding, n_results, where or None)
    return _json_query(embedding, n_results, where or None)


def delete_memory(memory_id: str) -> None:
    if not is_vector_store_enabled():
        return
    if _use_chroma():
        _chroma_delete(memory_id)
    else:
        _json_delete(memory_id)


def get_collection_count() -> int:
    if not is_vector_store_enabled():
        return 0
    if _use_chroma():
        return _chroma_count()
    return _json_count()
