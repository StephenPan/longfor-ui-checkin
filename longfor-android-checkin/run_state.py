"""Independent APP check-in state, Beijing timing and crash-safe persistence."""
from datetime import datetime, time as daytime, timedelta, timezone
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BEIJING = timezone(timedelta(hours=8))
TERMINAL = {"verified", "already_signed", "submission_intent", "uncertain"}


def now():
    return datetime.now(BEIJING)


def in_window(instant):
    current = instant.astimezone(BEIJING).time().replace(tzinfo=None)
    return daytime(6, 0) <= current < daytime(7, 30)


def blocks_reentry(state, date):
    return state.get("date") == date and state.get("status") in TERMINAL


def write_json(path, payload):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_json(path, default):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


def mark_submission():
    date = now().date().isoformat()
    path = ROOT / "state.json"
    if blocks_reentry(load_json(path, {}), date):
        raise RuntimeError("今日APP已提交或结果待核实，停止再次签到")
    state = {"date": date, "status": "submission_intent", "time": now().isoformat(), "channel": "APP"}
    write_json(path, state)
    return state
