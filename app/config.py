import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise ValueError(f"APP_PORT must be a valid integer, got: {value!r}")
    if not (1 <= port <= 65535):
        raise ValueError(f"APP_PORT must be between 1 and 65535, got: {port}")
    return port


class Config:
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = _parse_port(os.getenv("APP_PORT", "8001"))
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/memory.db")
    BACKUP_DIR: str = os.getenv("BACKUP_DIR", "data/backups")
    # Retention is generational, not a flat file count. BACKUP_KEEP_DAYS is how many
    # *days* keep a backup; BACKUP_KEEP_RECENT is how many of the newest backups stay
    # regardless of day. A flat count alone was unsafe: backups are taken on every
    # server start as well as from cron, so an evening of 14 restarts would fill all
    # 14 slots with same-day copies and evict the entire daily history. Keeping the
    # newest backup per day defends against that; keeping the few newest overall
    # defends the other direction, so a pre-migration snapshot isn't immediately
    # replaced by a post-migration one taken the same day.
    BACKUP_KEEP_DAYS: int = int(os.getenv("BACKUP_KEEP_DAYS", os.getenv("BACKUP_KEEP", "14")))
    BACKUP_KEEP_RECENT: int = int(os.getenv("BACKUP_KEEP_RECENT", "3"))
    API_KEY: str = os.getenv("API_KEY", "")

    # CORS used to be allow_origins=["*"], which let any page the browser had open read
    # this service's responses - /memory/list and /ui/export return the whole memory
    # store, so that was a read of every chat to any site the user happened to visit.
    # The default below keeps the one cross-origin caller that has to work (SillyTavern
    # on another localhost port) and drops the rest. Set CORS_ALLOW_ORIGINS to a
    # comma-separated list when ST is reached over something other than loopback, e.g.
    # a LAN address; doing so replaces the regex rather than adding to it.
    CORS_ALLOW_ORIGINS: list[str] = [
        o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()
    ]
    CORS_ALLOW_ORIGIN_REGEX: str = os.getenv(
        "CORS_ALLOW_ORIGIN_REGEX",
        r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
    )
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    LLM_API_BASE: str = os.getenv("LLM_API_BASE", "")
    # May hold a comma-separated failover pool: when one key hits its provider
    # quota (a 429), the client rotates to the next. A single key (no comma)
    # behaves exactly as before.
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    # A non-reasoning model on purpose. Reasoning models (the previous default,
    # zai-org/glm-4.7, among them) spend their token budget on hidden reasoning and return
    # an empty completion, so every structured-output caller - scene extraction, and now
    # trackers - saw a JSONDecodeError instead of a payload. Verified live against the
    # NanoGPT catalog: reasoning_tokens=0, clean JSON. See CLAUDE.md's
    # scene-extraction-llm-failing investigation.
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek/deepseek-v4-pro")
    LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "30"))

    # Trackers rewrite a whole document per call, so a reasoning model measurably runs
    # 15-35s on one - right at the 30s default, which produced ReadTimeouts that looked
    # like the model rejecting the schema. Sized with headroom, and retried once, since
    # the observed failures were transient rather than deterministic.
    # Scene extraction reads a whole scene and asks for schema'd JSON with a 6000-token
    # budget, but inherited the 30s default meant for short calls. Measured over
    # data/server.log on 2026-08-01: 568 fallbacks against 1583 successes - 26.4% of
    # extractions degraded to the rule-based path - with 200 ReadTimeouts as the single
    # largest cause. Sized like TRACKER_LLM_TIMEOUT, for the same reason: the budget has
    # to match the size of the call, not the average of all calls.
    SCENE_LLM_TIMEOUT: int = int(os.getenv("SCENE_LLM_TIMEOUT", "90"))

    # How much of a scene reaches the extraction LLM (see
    # llm_extractor.build_indexed_scene_text).
    #
    # SCENE_TEXT_MAX_CHARS used to be a hardcoded 4000 that the builder enforced by
    # `break`ing on the first message that did not fit. Measured over data/memory.db on
    # 2026-08-03: 41.3% of stored assistant messages are longer than 4000 characters on
    # their own, so whenever such a message opened a scene the builder returned an empty
    # string, extract_scene_facts returned None without logging, and the whole scene
    # silently degraded to the rule-based extractor - 536 of the 627 fallbacks in
    # data/server.log had no accompanying error, against only 91 real LLM failures.
    # SCENE_MESSAGE_MAX_CHARS caps each message individually (middle-out) so one long
    # message can no longer consume or block the whole scene budget.
    SCENE_TEXT_MAX_CHARS: int = int(os.getenv("SCENE_TEXT_MAX_CHARS", "12000"))
    SCENE_MESSAGE_MAX_CHARS: int = int(os.getenv("SCENE_MESSAGE_MAX_CHARS", "1500"))

    # Upper bound on what the rule-based extractor (extractor.extract_memories) may
    # store. That path keeps the source line close to verbatim, so on a descriptive
    # roleplay message it stores narrative prose ("Чай остывал в кружках, и пар больше
    # не поднимался над керамическими ободками...") as if it were a fact, and retrieval
    # then feeds the model back its own scenery. Measured over data/memory.db on
    # 2026-08-03: LLM-extracted facts top out at 251 characters (p99 = 197), while the
    # rule-based path runs to the 500-character truncation limit - of the 229 rows longer
    # than 250 characters, 228 came from the rule-based path. Cutting there drops prose
    # without touching anything the LLM path produces.
    RULE_EXTRACT_MAX_CONTENT_CHARS: int = int(os.getenv("RULE_EXTRACT_MAX_CONTENT_CHARS", "250"))
    # How many messages of an imported history make one scene for /memory/backfill.
    # Backfill used to run the rule-based extractor over the whole request, which stored
    # verbatim first-person lines ("Устала. Вчера была двойная смена...") as facts. It now
    # uses the same LLM scene path as a live turn, and a live turn is about this many
    # messages - the extension sends 8 by default. Whole-request extraction is not an
    # option: build_scene_text caps a scene at SCENE_TEXT_MAX_CHARS, so a long import
    # would have been silently truncated to its first few messages.
    BACKFILL_SCENE_SIZE: int = int(os.getenv("BACKFILL_SCENE_SIZE", "8"))

    TRACKER_LLM_TIMEOUT: int = int(os.getenv("TRACKER_LLM_TIMEOUT", "120"))
    TRACKER_LLM_MAX_TOKENS: int = int(os.getenv("TRACKER_LLM_MAX_TOKENS", "10000"))
    TRACKER_LLM_RETRIES: int = int(os.getenv("TRACKER_LLM_RETRIES", "1"))

    # The rolling summary layer existed for months and ran twice, because nothing called
    # it except a button. It now refreshes in the background after a store; these are the
    # knobs that decide how often that costs an LLM call. WINDOW is how many recent
    # episodic memories one summary covers, MIN_NEW how many fresh ones have to
    # accumulate before it is rewritten - the lower bound on cost, since a refresh that
    # is not due returns before reaching the model.
    ROLLING_SUMMARY_AUTO: bool = os.getenv("ROLLING_SUMMARY_AUTO", "true").lower() == "true"
    ROLLING_SUMMARY_WINDOW: int = int(os.getenv("ROLLING_SUMMARY_WINDOW", "8"))
    ROLLING_SUMMARY_MIN_NEW: int = int(os.getenv("ROLLING_SUMMARY_MIN_NEW", "3"))

    ACTIVE_LLM_PROVIDER: str = os.getenv("ACTIVE_LLM_PROVIDER", "nanogpt")

    OPENAI_API_BASE: str = os.getenv("OPENAI_API_BASE", "https://api.openai.com")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    ANTHROPIC_API_BASE: str = os.getenv("ANTHROPIC_API_BASE", "https://api.anthropic.com")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

    GOOGLE_API_KEYS: list[str] = [
        k.strip() for k in os.getenv("GOOGLE_API_KEYS", "").split(",") if k.strip()
    ]
    GOOGLE_EMBEDDING_MODEL: str = os.getenv("GOOGLE_EMBEDDING_MODEL", "gemini-embedding-2-preview")
    # The model returns 3072 dimensions by default and accepts outputDimensionality to
    # return fewer, already normalised (verified 2026-09-20: norm 1.0 at 768). 768 is
    # what makes the store affordable here - 4639 memories is 14 MB of float32 instead
    # of 57 MB, and the per-query cosine is a quarter of the arithmetic, on the retrieve
    # path that blocks generation. Changing this invalidates every stored vector: rows
    # whose dimensions differ from the query are skipped, so a change means a re-backfill.
    GOOGLE_EMBEDDING_DIM: int = int(os.getenv("GOOGLE_EMBEDDING_DIM", "768"))

    # Which service embeds. Measured 2026-09-21 on the 139-memory "Alina volkova" chat,
    # where every memory has a vector, against three lexically-disjoint paraphrase
    # queries ("где ты работаешь?" -> "работает в кафе Ботаника", and two more):
    #
    #   gemini-embedding-2-preview  the target ranked #107 of 139 - below the median
    #   bge-m3 (nanogpt)            #1, #1, #2 at +0.121 / +0.213 / +0.167 over median
    #   embed-multilingual-v3.0     #1, #1, #2 at +0.184 / +0.223 / +0.183
    #
    # Cohere wins because it is the only one that embeds a query and a document with
    # different functions (input_type), which is exactly the asymmetry here: a short
    # question against a declarative fact. It is not the default anyway - the trial key
    # allows 1000 requests a month, and a retrieve plus a store is two of them per turn,
    # so the memory layer would stop working mid-month. nanogpt has no monthly ceiling.
    #
    # None of them retrieves "предпочитает медленный темп" for "поедем ко мне?" - that
    # is an inference (home -> intimacy -> pace), not a similarity, and all three rank it
    # at #108. Embeddings close the paraphrase gap, not the inference one.
    EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "google")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "0"))
    COHERE_API_KEY: str = os.getenv("COHERE_API_KEY", "")

    CHROMADB_PATH: str = os.getenv("CHROMADB_PATH", "data/chromadb")

    def active_embedding_model(self) -> str:
        """The model name as stored on every row, so a switch is detectable.

        Falls back to the Google setting so an existing .env keeps working untouched.
        """
        if self.EMBEDDING_MODEL:
            return self.EMBEDDING_MODEL
        return self.GOOGLE_EMBEDDING_MODEL

    def active_embedding_dim(self) -> int:
        if self.EMBEDDING_DIM:
            return self.EMBEDDING_DIM
        return self.GOOGLE_EMBEDDING_DIM


config = Config()

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_security(cfg: Config = config) -> None:
    """Fail fast rather than silently serving with auth disabled on a public bind.

    APP_HOST defaults to 0.0.0.0, so an empty API_KEY (also the default) would
    otherwise leave every memory read/write endpoint open to anyone who can
    reach the host.
    """
    if cfg.APP_HOST not in LOOPBACK_HOSTS and not cfg.API_KEY:
        raise RuntimeError(
            f"Refusing to start: APP_HOST={cfg.APP_HOST!r} is not loopback and "
            "API_KEY is empty. Set API_KEY in .env, or set APP_HOST=127.0.0.1 "
            "for local-only use."
        )
