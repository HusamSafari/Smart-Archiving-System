# Telegram → Google Drive Archiving Bot

A production-ready Telegram bot that archives messages and files into Google Drive folders using topics. No local file storage; all content is uploaded in-memory. Runs headless with Service Account auth (no browser login).

## Features

- **Topics**: Set a current topic (e.g. `/work`, `/marketing`); each topic maps to a Google Drive folder.
- **Text**: Saved as `.txt` or Google Doc (configurable), with topic hashtag and username.
- **Media**: Photos, videos, voice messages, audio, and documents uploaded to the current topic folder.
- **Media groups**: Multiple files sent together are stored in a subfolder `Album_YYYYMMDD_HHMMSS`.
- **Reactions**: ✍️ processing, 👍 success, 🤷‍♂️ error.
- **Multi-user**: Per-user current topic; state persisted in `user_topics.json`.

## Requirements

- Python 3.11+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Google Cloud project with Drive API enabled and a Service Account
- Google Drive folders shared with the Service Account email

## Setup

### 1. Telegram Bot

1. Open [@BotFather](https://t.me/BotFather) and create a bot with `/newbot`.
2. Copy the token and set it as `TELEGRAM_BOT_TOKEN` in `.env`.

### 2. Google Drive (Service Account)

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or select one) and enable **Google Drive API**.
3. **APIs & Services → Credentials → Create credentials → Service account**.
4. Create the account and download its JSON key.
5. Share each target Drive folder with the Service Account email (e.g. `xxx@yyy.iam.gserviceaccount.com`) with **Editor** access.
6. Put the JSON path or its raw content in `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env`.

### 3. Configuration

1. Copy `.env.example` to `.env` and fill in:
   - `TELEGRAM_BOT_TOKEN`
   - `GOOGLE_SERVICE_ACCOUNT_JSON` (path like `./sa.json` or paste JSON string)
   - `DEFAULT_DRIVE_FOLDER_ID` (folder ID used when no topic is set)
2. Edit `topics.json`: add one object per topic with `name`, `hashtag`, `description`, `drive_folder_id`. Use the folder IDs of the Drive folders you shared with the Service Account.

Example `topics.json`:

```json
[
  {
    "name": "work",
    "hashtag": "#work",
    "description": "Work related content",
    "drive_folder_id": "YOUR_GOOGLE_DRIVE_FOLDER_ID"
  }
]
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from BotFather |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Yes | Path to SA JSON file or raw JSON string |
| `DEFAULT_DRIVE_FOLDER_ID` | Yes | Default Drive folder when no topic is set |
| `MAX_FILE_SIZE_BYTES` | No | Max file size (default 20971520 = 20 MB) |
| `ALLOWED_MIME_TYPES` | No | Comma-separated; empty = allow common types |
| `TEXT_FORMAT` | No | `txt` or `doc` (default `txt`) |
| `SEND_DETAILED_ERRORS` | No | `true` to send error details to users (default `false`) |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default `INFO`) |
| `TOPICS_FILE` | No | Path to topics JSON (default `topics.json`) |
| `USER_TOPICS_FILE` | No | Path to user topic state (default `user_topics.json`) |

## Run Locally

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and topics.json
python bot.py
```

## Run with Docker

```bash
cp .env.example .env
# Edit .env and topics.json
mkdir -p data
docker-compose up -d
```

- `./topics.json` is mounted so you can change topics without rebuilding.
- `./data` holds `user_topics.json` so user topic state persists across restarts.

## Usage

- `/start` — Welcome and short help.
- `/topic <name>` — Set current topic (e.g. `/topic work`).
- `/topics` — List topics.
- `/current` — Show your current topic.
- `/work`, `/marketing`, etc. — Set topic by command name (from `topics.json`).
- Send any **text** — Saved to the current topic folder (with hashtag and username).
- Send **photos, videos, voice, audio, documents** — Uploaded to the current topic folder; media groups go into a timestamped subfolder.

## Project Structure

```
├── bot.py              # Entry point, PTB handlers
├── drive_uploader.py    # Google Drive API (bytes-only uploads)
├── topic_manager.py    # topics.json + user_topics.json
├── topics.json         # Topic definitions
├── .env.example
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## Notes

- No files are stored on disk; content is streamed from Telegram to Drive in memory.
- Service Account only needs access to folders you explicitly share with it.
- For detailed errors in chat, set `SEND_DETAILED_ERRORS=true`.
