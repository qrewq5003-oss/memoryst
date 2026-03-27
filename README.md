# Memory Service

External memory service for SillyTavern.

## Requirements

- Python 3.10+

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_HOST` | `0.0.0.0` | Host to bind the server |
| `APP_PORT` | `8000` | Port to bind the server |
| `DATABASE_PATH` | `data/memory.db` | Path to SQLite database |
| `API_KEY` | `` | API key for authentication |
| `DEBUG` | `false` | Enable debug mode (auto-reload) |

## Running

```bash
# Development mode
python -m app.main

# Or with uvicorn directly
uvicorn app.main:app --host 0.0.0.0 --port 8000

# With custom configuration
APP_HOST=127.0.0.1 APP_PORT=9000 DATABASE_PATH=/path/to/db.sqlite python -m app.main
```

## Health Check

```bash
curl http://localhost:8000/health
```

Response:

```json
{"status": "ok"}
```

## Web UI

Access the built-in web UI at `http://localhost:8000/ui` for:
- Viewing and filtering memories
- Creating, editing, and deleting records
- Pin/unpin and archive/unarchive operations

## Rolling Summary CLI

Generate or update one rolling summary memory for a chat/character:

```bash
python scripts/run_rolling_summary.py --chat-id <chat_id> --character-id <character_id> --window 8
```
