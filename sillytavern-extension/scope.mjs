export function resolveEffectiveScope(rawContext = null) {
    if (!rawContext) {
        return null;
    }

    const chatId = rawContext.chatId || rawContext.groupId || 'default';
    const characterId = rawContext.characterId || chatId;

    return {
        chatId,
        characterId,
        // Display names, kept separate from the ids: characterId is a numeric index and
        // chatId only happens to start with the character's name. Extraction needs the
        // names themselves - without them the model writes "Девушка"/"Пользователь" into
        // stored facts and entities, and those phrasings never match a query.
        characterName: rawContext.name2 || null,
        userName: rawContext.name1 || null,
        groupId: rawContext.groupId || null,
        chat: rawContext.chat || [],
        chatScopeSource: rawContext.chatId ? 'chatId' : (rawContext.groupId ? 'groupId_fallback' : 'default_fallback'),
        characterScopeSource: rawContext.characterId ? 'characterId' : 'chatId_fallback',
        scopeKey: `${chatId}::${characterId}`,
    };
}
