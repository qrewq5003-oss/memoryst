import chromadb
from google import genai

from app.config import config

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None
_genai_client: genai.Client | None = None


def _get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(api_key=config.GOOGLE_API_KEY)
    return _genai_client


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
    return bool(config.GOOGLE_API_KEY)


def embed_text(text: str) -> list[float]:
    """Generate embedding for a single text using Google Generative AI."""
    client = _get_genai_client()
    result = client.models.embed_content(
        model=config.GOOGLE_EMBEDDING_MODEL,
        contents=text,
    )
    return result.embeddings[0].values


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts."""
    client = _get_genai_client()
    result = client.models.embed_content(
        model=config.GOOGLE_EMBEDDING_MODEL,
        contents=texts,
    )
    return [e.values for e in result.embeddings]


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
    """
    Find memories similar to the given text.

    Returns list of {id, distance, metadata} dicts.
    """
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
