# iMessage Insights

A local web app that analyzes your Mac's iMessage database to show who you text the most and what you talk about.

**All processing happens on your machine.** Nothing is uploaded or sent anywhere.

## Requirements

- macOS with Messages/iMessage
- Python 3.10+
- **Full Disk Access** for Terminal (or whatever app runs this) in **System Settings → Privacy & Security → Full Disk Access**

Without Full Disk Access, macOS blocks reads of `~/Library/Messages/chat.db`.

## Quick start

```bash
cd /Users/grantpatterson/Projects/imsg
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
