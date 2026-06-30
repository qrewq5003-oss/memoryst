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
- Focus on what changed, not what stayed the same
- If a section has no relevant info, write "No significant changes.\""""


def build_user_prompt(memories_text: str) -> str:
    """Build the user message with memory excerpts for the LLM."""
    return f"Recent episodic memories to analyze:\n\n{memories_text}"
