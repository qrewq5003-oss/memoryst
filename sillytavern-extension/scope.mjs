/**
 * Which chat and which character a turn belongs to.
 *
 * The distinction this module exists to keep straight: SillyTavern's
 * `getContext().characterId` is the character's **position in the characters array**, not
 * an identity. Adding, deleting or reordering a card shifts every index above it, and
 * anything that stored that number as "who this is" quietly started pointing at someone
 * else. Measured over memoryst's database on 2026-09-20: index 20 appeared in 15
 * unrelated chats, and 9 of 54 chats had their own memories split across two or three
 * indexes - one of them 360 memories in three unreachable pieces.
 *
 * So the index and the identity are two separate fields here:
 *
 * - `characterIndex` is the raw position, and is only for looking things up in
 *   SillyTavern's own runtime arrays. It must never be persisted.
 * - `characterId` is the avatar filename, which is what SillyTavern itself uses as a
 *   character's stable key everywhere it needs one. That is what goes to the backend.
 *
 * Retrieval no longer depends on this being right (the backend scopes by chat - see
 * text_utils.scope_character_id), so a wrong id can no longer hide a chat's history. It
 * still decides which character a tracker belongs to, and it is what any future
 * "what do I know about this character across chats" would have to key on.
 */

/**
 * The stable id of the character at `index` in SillyTavern's roster.
 *
 * Falls back through avatar → name → the index itself. The last step is the old,
 * unstable behaviour, kept deliberately: a context with no usable roster is rare and
 * temporary, and a turn stored under a shaky id is better than a turn stored under none.
 */
export function resolveStableCharacterId(characters, index) {
    if (index === null || index === undefined || index === '') {
        return null;
    }

    const entry = Array.isArray(characters) ? characters[Number(index)] : null;
    const avatar = entry?.avatar;
    if (typeof avatar === 'string' && avatar) {
        return avatar;
    }

    const name = entry?.name;
    if (typeof name === 'string' && name) {
        return name;
    }

    return String(index);
}

export function resolveEffectiveScope(rawContext = null) {
    if (!rawContext) {
        return null;
    }

    const chatId = rawContext.chatId || rawContext.groupId || 'default';
    const characterIndex = rawContext.characterId ?? null;
    const stableCharacterId = resolveStableCharacterId(rawContext.characters, characterIndex);

    return {
        chatId,
        // The identity. A group chat has no single character, so it falls back to the
        // chat itself, exactly as before - that is what makes group scopes collapse onto
        // one bucket rather than guessing at a member.
        characterId: stableCharacterId || chatId,
        // The position, for reading SillyTavern's own arrays. Never sent anywhere.
        characterIndex,
        // Display names, kept separate from the ids: extraction needs the names
        // themselves - without them the model writes "Девушка"/"Пользователь" into stored
        // facts and entities, and those phrasings never match a query.
        characterName: rawContext.name2 || null,
        userName: rawContext.name1 || null,
        groupId: rawContext.groupId || null,
        chat: rawContext.chat || [],
        chatScopeSource: rawContext.chatId ? 'chatId' : (rawContext.groupId ? 'groupId_fallback' : 'default_fallback'),
        // Says which rung of the fallback the id came from, so an audit record can show
        // whether this turn was stored under a stable key or a positional one.
        characterScopeSource: stableCharacterId
            ? (stableCharacterId === String(characterIndex) ? 'character_index_fallback' : 'character_avatar')
            : 'chatId_fallback',
        scopeKey: `${chatId}::${stableCharacterId || chatId}`,
    };
}
