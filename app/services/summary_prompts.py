SYSTEM_PROMPT = """You are analyzing a fragment of a roleplay conversation. Update long-term memory with a structured summary.

Write exactly three sections:

## Chronology
What happened (brief, factual, 2-3 sentences).

## Relationship Dynamics
How did the characters' relationship change? Trust, resentment, flirtation, tension, distance — note specific shifts.

## Mood and Subtext
What emotions did characters hide? What atmosphere dominates the scene? What unspoken tensions exist?

Rules:
- Write in the same language as the input (Russian or English)
- Be concise — max 150 words total
- ВАЖНО / IMPORTANT: your entire reply, all three sections combined, MUST be under 1200 characters total (not words — characters, including spaces and headers). This is a hard limit. Count as you write, and stop before you reach it even if a section feels unfinished. A reply over 1200 characters will be rejected.
- Focus on what changed, not what stayed the same
- If a section has no relevant info, write "No significant changes.\""""


def build_user_prompt(memories_text: str, conflict_notes: str = "") -> str:
    """Build the user message with memory excerpts for the LLM."""
    prompt = f"Recent episodic memories to analyze:\n\n{memories_text}"
    if conflict_notes:
        prompt += (
            "\n\nResolved fact updates (authoritative - describe the change, "
            "do not restate both versions as separate facts):\n\n" + conflict_notes
        )
    return prompt
