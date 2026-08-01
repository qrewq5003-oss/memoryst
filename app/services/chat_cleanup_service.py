"""One place that knows everything a chat owns.

Deleting a chat used to mean "delete the rows list_memories can see", which is a
strictly smaller set than "everything this chat owns", in two ways that both bit:

  - list_memories hides trackers by default, so a chat's trackers survived its
    deletion. A new chat created under the same id then inherited a stale timeline
    and relationship document from the previous one.
  - chat_messages was never touched at all - the repository had no delete function.
    The extracted facts went, the raw text stayed, and stayed reachable through
    retrieval's FTS fallback.

The two delete paths (the API endpoint and the UI form) had each grown their own
copy of the loop, which is how they drifted in the same direction. They now share
this one.
"""
from dataclasses import dataclass

from app.repositories.chat_message_repo import delete_chat_messages
from app.repositories.memory_repo import delete_memory, list_memories
from app.services import vector_store
from app.services.chat_buffer_service import drop_chat_buffer

# Deliberately generous: the largest live chat holds ~414 memories. A cap exists at
# all only so a corrupt scope can't try to load an unbounded result set into memory
# on a phone; delete_chat_data loops until the chat is actually empty, so reaching it
# costs another pass rather than a silently partial delete.
DELETE_PAGE_SIZE = 2000


@dataclass(frozen=True)
class ChatDeletionResult:
    deleted_memories: int
    deleted_trackers: int
    deleted_raw_messages: int


def delete_chat_data(chat_id: str, character_id: str | None = None) -> ChatDeletionResult:
    """Delete a chat's memories, trackers, vectors, raw messages and hot buffer.

    character_id is an optional narrowing filter, matching both endpoints: without
    it, every character in the chat is removed.
    """
    deleted_memories = 0
    deleted_trackers = 0

    while True:
        # include_trackers=True is the whole point: the default view is what let
        # trackers outlive their chat.
        batch = list_memories(
            chat_id=chat_id,
            character_id=character_id,
            limit=DELETE_PAGE_SIZE,
            include_trackers=True,
        ).items
        if not batch:
            break

        removed_in_batch = 0
        for item in batch:
            if not delete_memory(item.id):
                continue
            vector_store.delete_memory(item.id)
            removed_in_batch += 1
            if item.type == "tracker":
                deleted_trackers += 1
            else:
                deleted_memories += 1

        # Nothing in this batch could be deleted - stop rather than spin forever.
        if removed_in_batch == 0:
            break

    deleted_raw_messages = delete_chat_messages(chat_id, character_id)
    # Up to HOT_BUFFER_SIZE messages live only in memory; without this they would
    # cool straight back into the table this function just emptied.
    drop_chat_buffer(chat_id, character_id)

    return ChatDeletionResult(
        deleted_memories=deleted_memories,
        deleted_trackers=deleted_trackers,
        deleted_raw_messages=deleted_raw_messages,
    )
