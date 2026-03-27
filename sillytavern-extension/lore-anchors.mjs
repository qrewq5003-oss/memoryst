import { previewText } from './audit.mjs';

export const LORE_ANCHOR_PROMPT_KEY = 'memory-service-lore-anchor';
export const DEFAULT_MAX_LORE_ANCHOR_ITEMS = 1;
export const DEFAULT_MAX_LORE_ANCHOR_CHARS = 220;
export const LORE_ANCHOR_MARKER_PATTERNS = [
    /^\s*\[memory-anchor\]\s*$/im,
    /^\s*@memory-anchor(?:\s*:\s*(.+))?\s*$/im,
];

function normalizeWhitespace(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
}

function normalizeForDedupe(text) {
    return normalizeWhitespace(text).toLowerCase();
}

function getEntryCandidateText(entry = {}) {
    return (
        entry.content ??
        entry.entry ??
        entry.text ??
        entry.value ??
        ''
    );
}

function getEntryComment(entry = {}) {
    return entry.comment ?? entry.label ?? entry.title ?? entry.name ?? '';
}

function getEntryId(entry = {}) {
    return entry.uid ?? entry.id ?? entry.world_info_uid ?? getEntryComment(entry) ?? getEntryCandidateText(entry);
}

function getEntryTags(entry = {}) {
    const raw = entry.tags ?? entry.tag_list ?? entry.keytags ?? entry.extensions?.tags ?? [];
    if (Array.isArray(raw)) {
        return raw.map(tag => normalizeForDedupe(tag)).filter(Boolean);
    }
    if (typeof raw === 'string') {
        return raw
            .split(/[,\s]+/)
            .map(tag => normalizeForDedupe(tag))
            .filter(Boolean);
    }
    return [];
}

function hasMarker(text) {
    const source = String(text || '');
    return LORE_ANCHOR_MARKER_PATTERNS.some(pattern => pattern.test(source));
}

function getExplicitAnchorText(text) {
    const source = String(text || '');
    const match = source.match(/^\s*@memory-anchor\s*:\s*(.+)\s*$/im);
    return match?.[1] ? normalizeWhitespace(match[1]) : '';
}

function stripMarkerLines(text) {
    return String(text || '')
        .split('\n')
        .filter(line => !/^\s*(?:\[memory-anchor\]|@memory-anchor(?:\s*:.*)?)\s*$/i.test(line))
        .join('\n')
        .trim();
}

export function isAllowlistedLoreAnchorEntry(entry = {}) {
    const tags = getEntryTags(entry);
    if (tags.includes('memory-anchor')) {
        return true;
    }
    return hasMarker(getEntryComment(entry)) || hasMarker(getEntryCandidateText(entry));
}

export function extractLoreAnchorText(entry = {}) {
    const explicitCommentAnchor = getExplicitAnchorText(getEntryComment(entry));
    if (explicitCommentAnchor) {
        return explicitCommentAnchor;
    }

    const explicitContentAnchor = getExplicitAnchorText(getEntryCandidateText(entry));
    if (explicitContentAnchor) {
        return explicitContentAnchor;
    }

    const strippedContent = normalizeWhitespace(stripMarkerLines(getEntryCandidateText(entry)));
    if (strippedContent) {
        return strippedContent;
    }

    return normalizeWhitespace(stripMarkerLines(getEntryComment(entry)));
}

export function normalizeLoreAnchorCandidates(entries = []) {
    return (entries || [])
        .filter(entry => isAllowlistedLoreAnchorEntry(entry))
        .map(entry => ({
            id: String(getEntryId(entry) || ''),
            label: normalizeWhitespace(getEntryComment(entry)) || null,
            text: extractLoreAnchorText(entry),
            raw: entry,
        }))
        .filter(entry => entry.text);
}

function buildLoreAnchorLine(anchor) {
    return `- ${anchor.text}`;
}

function formatLoreAnchorBlock(anchors = []) {
    if (!anchors.length) {
        return '';
    }
    return ['[Lore Anchor]', ...anchors.map(buildLoreAnchorLine)].join('\n');
}

export function buildLoreAnchorBlock({
    entries = [],
    existingMemoryBlock = '',
    maxItems = DEFAULT_MAX_LORE_ANCHOR_ITEMS,
    maxChars = DEFAULT_MAX_LORE_ANCHOR_CHARS,
} = {}) {
    const candidates = normalizeLoreAnchorCandidates(entries);
    const selectedAnchors = [];
    const skipped = [];
    const seenTexts = new Set();
    const normalizedMemoryBlock = normalizeForDedupe(existingMemoryBlock);

    for (const candidate of candidates) {
        const normalizedText = normalizeForDedupe(candidate.text);
        if (!normalizedText) {
            skipped.push({ id: candidate.id, reason: 'empty' });
            continue;
        }
        if (seenTexts.has(normalizedText)) {
            skipped.push({ id: candidate.id, reason: 'duplicate_anchor' });
            continue;
        }
        if (normalizedMemoryBlock && normalizedMemoryBlock.includes(normalizedText)) {
            skipped.push({ id: candidate.id, reason: 'duplicate_memory_block' });
            continue;
        }

        selectedAnchors.push(candidate);
        seenTexts.add(normalizedText);
        if (selectedAnchors.length >= maxItems) {
            break;
        }
    }

    while (selectedAnchors.length > 0) {
        const anchorBlock = formatLoreAnchorBlock(selectedAnchors);
        if (anchorBlock.length <= maxChars) {
            return {
                anchorBlock,
                selectedAnchors,
                skipped,
                anchorItemCount: selectedAnchors.length,
                actualChars: anchorBlock.length,
                preview: previewText(anchorBlock),
            };
        }

        const removed = selectedAnchors.pop();
        skipped.push({ id: removed.id, reason: 'char_budget' });
    }

    return {
        anchorBlock: '',
        selectedAnchors: [],
        skipped,
        anchorItemCount: 0,
        actualChars: 0,
        preview: '',
    };
}
