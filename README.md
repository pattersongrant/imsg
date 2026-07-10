# iMessage Insights
A local web app that analyzes your Mac's iMessage database to show who you text the most and what you talk about.
<img width="942" height="801" alt="Screenshot 2026-07-10 at 12 59 49 AM" src="https://github.com/user-attachments/assets/b7fd6e29-aaed-4cb3-9fbf-7bd68aa5acb1" />
<img width="832" height="487" alt="Screenshot 2026-07-10 at 12 59 56 AM" src="https://github.com/user-attachments/assets/f6b561a5-c0d4-4bff-bbdc-d61ddcd805db" />


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

## Optional: merge exports from Google Cloud Storage

If you store exported iMessage rows in GCS (JSON or JSONL), the app can merge per-person counts with your local database.

```bash
export GCS_BUCKET=your-bucket-name
export GCS_PREFIX=imessage-exports/   # optional folder prefix
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
python app.py
```

Each export file should contain rows like:

```json
{"handle": "+15551234567", "name": "Alex", "text": "hey!", "is_from_me": true, "date": "2024-06-01T12:00:00"}
```

When configured, `/api/contacts` and `/api/bubbles` automatically merge local + GCS stats. Use `?gcs=0` to disable merging. GCS-only stats are available at `/api/gcs/contacts`.
