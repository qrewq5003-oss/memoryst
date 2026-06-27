#!/usr/bin/env python3
"""Telegram bot for memoryst memory management."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from app.db import get_connection, init_schema
from app.repositories.memory_repo import list_memories, get_memory_by_id, delete_memory
from app.services.summary_service import generate_rolling_summary
from app.services.store_service import store_memories
from app.schemas import StoreMemoryRequest, MessageInput

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DEFAULT_CHAT_ID = os.getenv("TELEGRAM_DEFAULT_CHAT", "telegram")
DEFAULT_CHAR_ID = os.getenv("TELEGRAM_DEFAULT_CHAR", "assistant")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Memory Service bot\n\n"
        "/list [chat_id] — show memories\n"
        "/summary [chat_id] [char_id] — generate summary\n"
        "/store <text> — store a memory\n"
        "/stats — memory stats\n"
        "/help — this message\n\n"
        "Or just send a message to store it as a memory."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.args[0] if context.args else DEFAULT_CHAT_ID
    memories = list_memories(chat_id=chat_id, limit=10, archived=False)

    if not memories.items:
        await update.message.reply_text(f"No memories for chat '{chat_id}'.")
        return

    lines = [f"Memories for <b>{chat_id}</b> ({memories.total} total):\n"]
    for m in memories.items:
        pin = "📌 " if m.pinned else ""
        lines.append(f"• {pin}<code>{m.id[:8]}</code> [{m.layer}] {m.content[:80]}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.args[0] if context.args else DEFAULT_CHAT_ID
    char_id = context.args[1] if len(context.args) > 1 else DEFAULT_CHAR_ID

    await update.message.reply_text("Generating summary...")

    result = generate_rolling_summary(chat_id=chat_id, character_id=char_id)

    response = (
        f"<b>Summary</b> ({result.action})\n"
        f"Chat: {result.chat_id} | Char: {result.character_id}\n"
        f"Episodes: {result.summarized_count} | New: {result.new_input_count}\n\n"
        f"{result.summary_text}"
    )
    await update.message.reply_text(response, parse_mode="HTML")


async def cmd_store(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args) if context.args else None
    if not text:
        await update.message.reply_text("Usage: /store <text to remember>")
        return

    request = StoreMemoryRequest(
        chat_id=DEFAULT_CHAT_ID,
        character_id=DEFAULT_CHAR_ID,
        messages=[MessageInput(role="user", text=text)],
    )
    result = store_memories(request)
    await update.message.reply_text(
        f"Stored: {result.stored} | Updated: {result.updated} | Skipped: {result.skipped}"
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM memories WHERE archived = 0")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM memories WHERE archived = 0 AND layer = 'episodic'")
    episodic = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM memories WHERE archived = 0 AND layer = 'stable'")
    stable = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM memories WHERE archived = 0 AND type = 'summary'")
    summaries = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT chat_id) FROM memories WHERE archived = 0")
    chats = cursor.fetchone()[0]

    conn.close()

    await update.message.reply_text(
        f"<b>Memory Stats</b>\n"
        f"Total: {total} | Episodic: {episodic} | Stable: {stable}\n"
        f"Summaries: {summaries} | Chats: {chats}",
        parse_mode="HTML",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store incoming messages as memories."""
    text = update.message.text
    if not text or len(text) < 10:
        return

    request = StoreMemoryRequest(
        chat_id=DEFAULT_CHAT_ID,
        character_id=DEFAULT_CHAR_ID,
        messages=[MessageInput(role="user", text=text)],
    )
    result = store_memories(request)
    if result.stored > 0:
        await update.message.reply_text(f"Remembered. ({result.stored} new)")


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("list", "List memories [chat_id]"),
        BotCommand("summary", "Generate summary [chat_id] [char_id]"),
        BotCommand("store", "Store a memory"),
        BotCommand("stats", "Memory statistics"),
        BotCommand("help", "Show help"),
    ])


def main() -> None:
    if not BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not set in .env")
        print("Get a token from @BotFather on Telegram")
        sys.exit(1)

    init_schema()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("store", cmd_store))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Telegram bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
