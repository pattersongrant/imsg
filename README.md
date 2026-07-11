# iMessage Insights
A local web app that analyzes your Mac's iMessage database to show who you text the most and what you talk about.
<img width="1424" height="811" alt="Screenshot 2026-07-10 at 10 54 08 PM" src="https://github.com/user-attachments/assets/098d02ab-1e34-432e-bc0f-0b0ec15bf7ec" />
<img width="834" height="424" alt="Screenshot 2026-07-10 at 10 56 00 PM" src="https://github.com/user-attachments/assets/e868261c-cd96-440a-9797-6810fb8d68d9" />
<img width="832" height="487" alt="Screenshot 2026-07-10 at 12 59 56 AM" src="https://github.com/user-attachments/assets/f6b561a5-c0d4-4bff-bbdc-d61ddcd805db" />

(Anonymized Preview, texts and names are hidden!)


**All processing happens on your machine.** Nothing is uploaded or sent anywhere.

## Requirements

- macOS with Messages/iMessage
- Python 3.10+
- **Full Disk Access** for Terminal (or whatever app runs this) in **System Settings → Privacy & Security → Full Disk Access**

Without Full Disk Access, macOS blocks reads of `~/Library/Messages/chat.db`.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5050](http://127.0.0.1:5050) in your browser.

## What it shows

- **Top contacts** — ranked by message count (sent + received), with breakdown
- **Activity over time** — monthly message volume
- **Topics** — most common meaningful words overall and per contact
- **Conversation drill-down** — sample recent messages for any contact

## Optional: custom database path

```bash
IMESSAGE_DB_PATH=/path/to/chat.db python app.py
```

Default path: `~/Library/Messages/chat.db`

## Optional: auto-index from Google Cloud Storage

If you sync `chat.db` backups (or JSON exports) to GCS, the app **automatically indexes them on startup** and merges them into contacts, topics, and keywords. No manual export step is required if you upload `chat.db` files.

```bash
export GCS_BUCKET=your-bucket-name
export GCS_PREFIX=imessage-backups/   # optional folder prefix
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
python app.py
```

Supported files in the bucket:
- `chat.db` — full iMessage database backups (recommended)
- `.json` / `.jsonl` — optional row exports

The index refreshes every 15 minutes by default (`GCS_REFRESH_SECONDS=900`). Set `GCS_REFRESH_SECONDS=0` to only index at startup.

When configured, local stats plus GCS data are merged automatically in:
- Top Contacts and People Map
- Topics tab (per-person keywords)
- Contact drill-down top words

Use `?gcs=0` on API calls to disable merging. GCS-only stats: `/api/gcs/contacts`.
