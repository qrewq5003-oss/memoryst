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
| `APP_PORT` | `8001` | Port to bind the server |
| `DATABASE_PATH` | `data/memory.db` | Path to SQLite database |
| `BACKUP_DIR` | `data/backups` | Directory for timestamped database backups |
| `BACKUP_KEEP` | `14` | Number of backups to retain (oldest are deleted first) |
| `API_KEY` | `` | API key for the `/memory` API (empty = auth disabled). Sent as the `X-API-Key` header. |
| `DEBUG` | `false` | Enable debug mode (auto-reload) |

## Security

Authentication is **opt-in**: leave `API_KEY` empty for the local-only default
(no header required, behaves as before). When `API_KEY` is set, every `/memory`
API endpoint requires a matching `X-API-Key` header. `/health` and
`/memory/version` stay unauthenticated on purpose (diagnostic handshake).

The server refuses to start if `APP_HOST` is non-loopback (e.g. `0.0.0.0`) while
`API_KEY` is empty — this closes the silent "public bind, no auth" hole.

> ⚠️ **Before exposing this service on a LAN, know the current limit:** `API_KEY`
> protects only the `/memory` API. The web UI router (`/ui` and its form-based
> create/edit/delete/pin/archive actions) is **not** behind the key, and the
> browser Tools tab calls `/memory/*` via `fetch()` without the header — so with
> `API_KEY` set those Tools actions return 401. For localhost use this is fine.
> **If you enable `API_KEY` for non-localhost access, first protect the UI router
> (`app/routes/ui.py`) — do not rely on `API_KEY` alone for LAN exposure.**

## Running

```bash
# Development mode
python -m app.main

# Or with uvicorn directly
uvicorn app.main:app --host 0.0.0.0 --port 8001

# With custom configuration
APP_HOST=127.0.0.1 APP_PORT=9000 DATABASE_PATH=/path/to/db.sqlite python -m app.main
```

## Health Check

```bash
curl http://localhost:8001/health
```

Response:

```json
{"status": "ok"}
```

## Web UI

Access the built-in web UI at `http://localhost:8001/ui` for:
- Browsing memories grouped by `chat_id + character_id` in a chat sidebar
- Defaulting to one selected chat scope instead of mixing all chats together
- Switching to an `All Chats` view when you need the global list
- Showing friendlier chat labels in the sidebar while keeping raw IDs visible
- Highlighting the currently selected chat scope or `All Chats` state
- Viewing and filtering memories inside the selected chat scope
- Creating, editing, and deleting records
- Pin/unpin and archive/unarchive operations

The chat folders are a UI grouping layer over the existing scoped storage. They do not create physical folders or separate databases per chat.

## Database Backups

The whole memory store is a single SQLite file (`data/memory.db`), so losing
the device means losing all memories unless a backup exists.

A timestamped, consistent snapshot (via SQLite's online backup API, safe to
take while the server is running) is written to `data/backups/` automatically
every time the server starts, keeping the `BACKUP_KEEP` most recent copies.

To also get a backup on days the server never restarts, run it from cron
(Termux: `pkg install cronie`, then `crontab -e`):

```bash
0 4 * * * cd ~/memoryst && .venv/bin/python scripts/backup_db.py >> data/backup.log 2>&1
```

**Known Android limitation:** `crond` does not start automatically on boot in
Termux - a device reboot silently kills the cron daemon, and no crontab entry
fires again until something runs `crond` manually. Standard cron tooling has
no fix for this (it assumes an init system, which Android's Termux sandbox
doesn't give it).

`scripts/termux-boot.sh` now starts `crond` itself (guarded with
`pgrep -x crond || crond`, so re-running the hook never spawns a second
daemon) right after it starts the memoryst server, so both come back up
together on every reboot - **but only if the separate Termux:Boot app is
installed** (F-Droid or Google Play; it is not the same thing as the
`termux-boot` *package* installed via `pkg install termux-boot`, which is
also required). Without the Termux:Boot **app** installed and opened once to
grant its permissions, Android never invokes anything under
`~/.termux/boot/` - the hook script can be perfectly correct and still never
run. If you skipped that step, cron and the server will both stay dead after
every reboot until you start them by hand:

```bash
cd ~/memoryst && .venv/bin/python -m app.main &   # server
crond                                              # cron daemon
```

Or run it manually any time:

```bash
python scripts/backup_db.py
```

Backups currently live on the same device/disk as the live database, so they
protect against accidental deletion or a bad consolidation run, but not
against device loss, theft, or destruction. Syncing `data/backups/` to
somewhere off-device (cloud storage, another machine) is a good next step,
not yet implemented.

## Rolling Summary CLI

Generate or update one rolling summary memory for a chat/character:

```bash
python scripts/run_rolling_summary.py --chat-id <chat_id> --character-id <character_id> --window 8 --min-new 3
```

`--min-new` is the refresh policy knob for experiments/eval. Default is `3`.

The CLI reports one of:

- `created`: first rolling summary was created
- `updated`: enough new episodic memories accumulated, so the summary was refreshed
- `skipped_not_enough_inputs`: there are still too few episodic memories to build a useful summary
- `skipped_not_enough_new_inputs`: the existing summary is still fresh enough; not enough new episodic inputs accumulated yet

It also prints:

- `summarized_count`
- `new_input_count`
- `refresh_threshold_used`
- `source_memory_ids`

## Live ST Verification

Short practical notes from the first real SillyTavern runtime verification run are in [docs/live_st_verification_report.md](docs/live_st_verification_report.md).
The repeated verification pass after the Russian relationship retrieval fixes is in [docs/live_st_verification_report_v2.md](docs/live_st_verification_report_v2.md).
The local-scene focused repeat verification pass is in [docs/live_st_verification_report_v3.md](docs/live_st_verification_report_v3.md).
The richer seeded relationship-arc verification is in [docs/live_st_relationship_arc_report.md](docs/live_st_relationship_arc_report.md).
The durable relationship formation live verification is in [docs/live_st_durable_relationship_report.md](docs/live_st_durable_relationship_report.md).
The focused re-check after the question-form durable relationship guardrail is in [docs/live_st_question_guardrail_recheck.md](docs/live_st_question_guardrail_recheck.md).
The focused re-check after the question-form local-scene guardrail is in [docs/live_st_local_scene_question_recheck.md](docs/live_st_local_scene_question_recheck.md).
The first NanoGPT model adaptation pass for `GLM 4.7` and `Kimi K2.5` is in [docs/model_adaptation_report_nanogpt.md](docs/model_adaptation_report_nanogpt.md).
The full-prompt follow-up check for `GLM 4.7` and `Kimi K2.5` is in [docs/full_prompt_model_adaptation_report.md](docs/full_prompt_model_adaptation_report.md).
The lorebook-triggered ephemeral anchor bridge note is in [docs/lorebook_ephemeral_anchor_report.md](docs/lorebook_ephemeral_anchor_report.md).
The short live usefulness check for lorebook-triggered ephemeral anchors is in [docs/lorebook_anchor_usefulness_report.md](docs/lorebook_anchor_usefulness_report.md).
The real marked world-info live usefulness check is in [docs/lorebook_worldinfo_live_usefulness_report.md](docs/lorebook_worldinfo_live_usefulness_report.md).
The payload-level debug pass for the real world-info activation mismatch is in [docs/lorebook_worldinfo_payload_debug_report.md](docs/lorebook_worldinfo_payload_debug_report.md).
The post-fix usefulness re-check for the real installed lorebook anchor path is in [docs/lorebook_anchor_usefulness_recheck_report.md](docs/lorebook_anchor_usefulness_recheck_report.md).
