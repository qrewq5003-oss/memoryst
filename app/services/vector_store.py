import json
import threading
from pathlib import Path

import chromadb
import httpx

from app.config import config

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None

_key_lock = threading.Lock()
_keys: list[str] = []
_key_index: int = 0

KEYS_FILE = Path(config.CHROMADB_PATH).parent / "google_keys.json"

EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"


def _load_keys() -> list[str]:
    """Load keys from file, falling back to config."""
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
    """Persist current keys to file."""
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
    """Rotate to the next API key. Returns False if no more keys."""
    global _key_index
    with _key_lock:
        if len(_keys) <= 1:
            return False
        _key_index = (_key_index + 1) % len(_keys)
        return True


def _call_embed(text: str | list[str]) -> list[list[float]]:
    """Call Google embeddings API directly with httpx."""
    _ensure_keys()
    if not _keys:
        raise RuntimeError("No Google API keys configured")

    url = EMBED_URL.format(model=config.GOOGLE_EMBEDDING_MODEL)

    attempts = len(_keys)
    for attempt in range(attempts):
        key = _get_active_key()
        api_url = f"{url}?key={key}"

        if isinstance(text, list):
            payload = {"requests": [{"model": f"models/{config.GOOGLE_EMBEDDING_MODEL}", "content": {"parts": [{"text": t}]}} for t in text]}
            resp = httpx.post(api_url, json=payload, timeout=30)
        else:
            payload = {"model": f"models/{config.GOOGLE_EMBEDDING_MODEL}", "content": {"parts": [{"text": text}]}}
            resp = httpx.post(api_url, json=payload, timeout=30)

        if resp.status_code == 200:
            data = resp.json()
            if isinstance(text, list):
                return [r["embedding"]["values"] for r in data["embeddings"]]
            else:
                return [data["embedding"]["values"]]

        if resp.status_code == 429:
            if attempt < attempts - 1 and _rotate_key():
                continue
            raise RuntimeError(f"Rate limited on all {attempts} keys: {resp.text}")

        raise RuntimeError(f"Embedding API error {resp.status_code}: {resp.text}")

    raise RuntimeError("All API keys exhausted")


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=config.CHROMADB_PATH)
    return _client


def _get_collection() -> chromadb.Collection:
    global _collection
    if _collection is None:
        _collection = _get_client().get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


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
    """Generate embedding for a single text."""
    return _call_embed(text)[0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts."""
    return _call_embed(texts)


def add_key(key: str) -> None:
    """Add a new API key to the pool."""
    _ensure_keys()
    with _key_lock:
        if key not in _keys:
            _keys.append(key)
            _save_keys()


def remove_key(key: str) -> bool:
    """Remove an API key from the pool."""
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
    """List all keys (masked) with active indicator."""
    _ensure_keys()
    with _key_lock:
        result = []
        for i, k in enumerate(_keys):
            masked = f"{k[:8]}...{k[-4:]}" if len(k) > 12 else "***"
            result.append({"masked": masked, "active": i == _key_index})
        return result


def add_memory(memory_id: str, content: str, metadata: dict | None = None) -> None:
    """Store a memory's embedding in the vector store."""
    if not is_vector_store_enabled():
        return

    embedding = embed_text(content)
    collection = _get_collection()
    collection.upsert(
        ids=[memory_id],
        embeddings=[embedding],
        metadatas=[metadata or {}],
    )


def query_similar(
    text: str,
    *,
    n_results: int = 10,
    chat_id: str | None = None,
    character_id: str | None = None,
) -> list[dict]:
    """Find memories similar to the given text."""
    if not is_vector_store_enabled():
        return []

    embedding = embed_text(text)
    collection = _get_collection()

    count = collection.count()
    if count <= 0:
        return []

    query_kwargs: dict = {
        "query_embeddings": [embedding],
        "n_results": min(n_results, count),
    }

    where_conditions = []
    if chat_id:
        where_conditions.append({"chat_id": chat_id})
    if character_id:
        where_conditions.append({"character_id": character_id})
    if len(where_conditions) == 1:
        query_kwargs["where"] = where_conditions[0]
    elif len(where_conditions) > 1:
        query_kwargs["where"] = {"$and": where_conditions}

    results = collection.query(**query_kwargs)

    items = []
    for i in range(len(results["ids"][0])):
        items.append({
            "id": results["ids"][0][i],
            "distance": results["distances"][0][i],
            "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
        })
    return items


def delete_memory(memory_id: str) -> None:
    """Remove a memory's embedding from the vector store."""
    if not is_vector_store_enabled():
        return

    collection = _get_collection()
    try:
        collection.delete(ids=[memory_id])
    except Exception:
        pass


def get_collection_count() -> int:
    """Return the number of embeddings in the store."""
    if not is_vector_store_enabled():
        return 0
    return _get_collection().count()
