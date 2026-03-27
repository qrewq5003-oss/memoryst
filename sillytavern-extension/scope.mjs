export function resolveEffectiveScope(rawContext = null) {
    if (!rawContext) {
        return null;
    }

    const chatId = rawContext.chatId || rawContext.groupId || 'default';
    const characterId = rawContext.characterId || chatId;

    return {
        chatId,
        characterId,
        groupId: rawContext.groupId || null,
        chat: rawContext.chat || [],
        chatScopeSource: rawContext.chatId ? 'chatId' : (rawContext.groupId ? 'groupId_fallback' : 'default_fallback'),
        characterScopeSource: rawContext.characterId ? 'characterId' : 'chatId_fallback',
        scopeKey: `${chatId}::${characterId}`,
    };
}
