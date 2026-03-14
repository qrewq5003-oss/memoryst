import re
from datetime import datetime, timezone

from app.repositories.memory_repo import list_memories
from app.schemas import MemoryItem, RetrieveMemoryRequest, RetrieveMemoryResponse
from app.services.formatter import format_memory_block


def _extract_keywords(text: str) -> list[str]:
    """Extract keywords from text for matching."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    words = text.split()
    stopwords = {
        "что", "как", "так", "вот", "уже", "еще", "ещё", "был", "была", "было", "были",
        "the", "and", "but", "for", "with", "from", "this", "that", "have", "has", "had",
        "was", "were", "been", "being", "are", "is", "was", "about", "just", "into",
    }
    keywords = []
    seen = set()
    for word in words:
        if len(word) >= 3 and word not in stopwords and word not in seen:
            keywords.append(word)
            seen.add(word)
    return keywords


def _extract_entities(text: str) -> list[str]:
    """Extract entities from text (capitalized words)."""
    words = re.findall(r"\b([A-ZА-Я][a-zа-яё]+)\b", text)
    seen = set()
    entities = []
    for word in words:
        lower = word.lower()
        if lower not in seen and len(word) > 1:
            seen.add(lower)
            entities.append(lower)
    return entities


def _compute_score(
    memory: MemoryItem,
    input_keywords: list[str],
    input_entities: list[str],
) -> float:
    """
    Compute relevance score for a memory item.

    Formula:
    - keyword_overlap: intersection / max(1, len(input_keywords))
    - entity_overlap: intersection / max(1, len(input_entities))
    - recency: 1 / (1 + days_since_created)
    - base_score = keyword_overlap * 0.35 + entity_overlap * 0.30 + importance * 0.20 + recency * 0.10
    - if pinned: score = max(base_score, 0.4)
    - cap at 1.0
    """
    # Keyword overlap
    memory_keywords = set(memory.metadata.keywords)
    input_kw_set = set(input_keywords)
    if len(input_kw_set) > 0:
        keyword_overlap = len(memory_keywords & input_kw_set) / len(input_kw_set)
    else:
        keyword_overlap = 0.0

    # Entity overlap
    memory_entities = set(e.lower() for e in memory.metadata.entities)
    input_ent_set = set(input_entities)
    if len(input_ent_set) > 0:
        entity_overlap = len(memory_entities & input_ent_set) / len(input_ent_set)
    else:
        entity_overlap = 0.0

    # Recency: 1 / (1 + days_since_created)
    try:
        created = datetime.fromisoformat(memory.created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_since = (now - created).days
        recency = 1.0 / (1.0 + days_since)
    except (ValueError, TypeError):
        recency = 0.5  # Default if parsing fails

    # Base score
    base_score = (
        keyword_overlap * 0.35 +
        entity_overlap * 0.30 +
        memory.importance * 0.20 +
        recency * 0.10
    )

    # Pinned floor
    if memory.pinned:
        score = max(base_score, 0.4)
    else:
        score = base_score

    # Cap at 1.0
    return min(score, 1.0)


def retrieve_memories(request: RetrieveMemoryRequest) -> RetrieveMemoryResponse:
    """
    Retrieve relevant memories for the current context.

    Algorithm:
    1. Get candidates via list_memories
    2. Compute score for each
    3. Filter out zero-score items
    4. Sort by score DESC
    5. Take top-k
    6. Format memory block
    """
    # Extract keywords and entities from user_input
    input_keywords = _extract_keywords(request.user_input)
    input_entities = _extract_entities(request.user_input)

    # Also consider recent_messages
    for msg in request.recent_messages:
        input_keywords.extend(_extract_keywords(msg.text))
        input_entities.extend(_extract_entities(msg.text))

    # Get candidates
    result = list_memories(
        chat_id=request.chat_id,
        character_id=request.character_id,
        archived=None if request.include_archived else False,
        limit=200,  # Get enough candidates for scoring
        offset=0,
    )

    all_candidates = result.items
    total_candidates = len(all_candidates)

    # Score each candidate
    scored = []
    for memory in all_candidates:
        score = _compute_score(memory, input_keywords, input_entities)
        if score > 0:
            scored.append((score, memory))

    # Sort by score DESC
    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top-k
    top_items = [item for _, item in scored[: request.limit]]

    # Format memory block
    memory_block = format_memory_block(top_items)

    return RetrieveMemoryResponse(
        items=top_items,
        memory_block=memory_block,
        total_candidates=total_candidates,
    )
