"""Estimate time spent messaging from message counts."""

from __future__ import annotations

# Rough averages: time to type vs read a typical text.
SENT_MESSAGE_SECONDS = 20
RECEIVED_MESSAGE_SECONDS = 12


def estimate_seconds(*, sent: int = 0, received: int = 0, total: int | None = None) -> int:
    if sent or received:
        return int(sent * SENT_MESSAGE_SECONDS + received * RECEIVED_MESSAGE_SECONDS)
    if total is not None and total > 0:
        avg = (SENT_MESSAGE_SECONDS + RECEIVED_MESSAGE_SECONDS) / 2
        return int(total * avg)
    return 0


def format_duration(seconds: int | float) -> str:
    if seconds <= 0:
        return "0 min"
    if seconds < 3600:
        minutes = max(1, round(seconds / 60))
        return f"{minutes} min"
    if seconds < 86400 * 2:
        hours = seconds / 3600
        text = f"{hours:.1f} hr"
        return text.replace(".0 hr", " hr")
    if seconds < 86400 * 60:
        days = seconds / 86400
        text = f"{days:.1f} days"
        return text.replace(".0 days", " days")
    if seconds < 86400 * 365:
        weeks = seconds / (86400 * 7)
        text = f"{weeks:.1f} wk"
        return text.replace(".0 wk", " wk")
    years = seconds / (86400 * 365)
    text = f"{years:.1f} yr"
    return text.replace(".0 yr", " yr")


def estimate_summary(*, sent: int, received: int, total: int) -> dict:
    seconds = estimate_seconds(sent=sent, received=received, total=total)
    return {
        "time_seconds": seconds,
        "time_display": format_duration(seconds),
        "time_note": f"~{SENT_MESSAGE_SECONDS}s sent · ~{RECEIVED_MESSAGE_SECONDS}s received per message",
    }
