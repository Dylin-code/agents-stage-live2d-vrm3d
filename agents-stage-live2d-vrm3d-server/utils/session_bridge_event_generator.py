#!/usr/bin/env python3
"""Generate synthetic Codex session jsonl files for session-bridge load testing."""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


STATE_EVENTS = [
    ("event_msg", {"type": "agent_reasoning", "text": "thinking"}),
    ("response_item", {"type": "function_call", "name": "exec_command"}),
    ("event_msg", {"type": "agent_message", "message": "responding"}),
]


def iso_ts(base: datetime, offset_ms: int) -> str:
    ts = base + timedelta(milliseconds=offset_ms)
    return ts.isoformat().replace("+00:00", "Z")


def write_session_file(path: Path, session_id: str, events_per_session: int, seed: int) -> None:
    rnd = random.Random(seed)
    base = datetime.now(timezone.utc)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        meta = {
            "timestamp": iso_ts(base, 0),
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "originator": "Codex Desktop",
                "cwd": "/tmp/session-bridge-load-test",
            },
        }
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")

        for idx in range(events_per_session):
            event_type, payload = rnd.choice(STATE_EVENTS)
            line = {
                "timestamp": iso_ts(base, (idx + 1) * 10),
                "type": event_type,
                "payload": payload,
            }
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

        done = {
            "timestamp": iso_ts(base, (events_per_session + 1) * 10),
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": session_id},
        }
        f.write(json.dumps(done, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic Codex jsonl events.")
    parser.add_argument("--output-dir", required=True, help="Directory to write jsonl files")
    parser.add_argument("--sessions", type=int, default=12, help="Number of sessions")
    parser.add_argument("--events", type=int, default=10000, help="Total number of events")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    per_session = max(1, args.events // max(1, args.sessions))
    for idx in range(args.sessions):
        session_id = f"00000000-0000-0000-0000-{idx:012d}"
        file_path = output_dir / "2026" / "02" / "27" / f"rollout-2026-02-27T00-00-00-{session_id}.jsonl"
        write_session_file(file_path, session_id, per_session, seed=idx)

    print(f"Generated {args.sessions} session files in {output_dir}")
    print(f"Approx events per session: {per_session}")


if __name__ == "__main__":
    main()
