"""Live follower: continuously tail Untapped's log.log and record everything.

Independent of the agent session -- run it in a real console window and it keeps
going.  Writes:
    tmp/live_follow.jsonl        one JSON object per line (raw log line + wall time)
    tmp/live_follow_events.jsonl only the interesting markers, tagged
    tmp/live_follow_state.json   tailer cursor (atomic)

Usage:
    python tools/live_follow.py                 # start_from='end'  (only new lines)
    python tools/live_follow.py --beginning     # replay whole files first
    python tools/live_follow.py --interval 1
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.log_tailer import LogTailer  # noqa: E402

# Markers worth surfacing separately.  Order matters: first match wins.
PATTERNS = [
    ("COIN", "is deciding whether to go first"),
    ("DUEL_START", "Starting Duel"),
    ("TURN1", "Turn 1 for player"),
    ("TURN2", "Turn 2 for player"),
    ("GAME_ENDED", "Game ended:"),
    ("UPLOAD", "[Ygom.uploadGame] Uploading game"),
    ("ROTATE", "__ROTATION__"),
]


def classify(line: str):
    for tag, pat in PATTERNS:
        if pat in line:
            return tag
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--beginning", action="store_true",
                    help="read from start of files instead of only new data")
    ap.add_argument("--outdir", default=os.path.join(_ROOT, "tmp"))
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    raw_path = os.path.join(args.outdir, "live_follow.jsonl")
    ev_path = os.path.join(args.outdir, "live_follow_events.jsonl")
    state_path = os.path.join(args.outdir, "live_follow_state.json")

    start_from = "beginning" if args.beginning else "end"
    tailer = LogTailer(start_from=start_from, state_path=state_path)

    raw_f = open(raw_path, "a", encoding="utf-8")
    ev_f = open(ev_path, "a", encoding="utf-8", buffering=1)

    def note(obj):
        obj["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        ev_f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    note({"tag": "start", "start_from": start_from,
          "status": tailer.status()})
    print(f"[live_follow] start_from={start_from} interval={args.interval}s", flush=True)
    print(f"[live_follow] raw   -> {raw_path}", flush=True)
    print(f"[live_follow] events-> {ev_path}", flush=True)

    seen = set()
    try:
        while True:
            # poll_events() 返回的是 list[str]（已去掉换行、只保留 '['
            # 开头的有效行），不是事件对象。
            for stripped in tailer.poll_events():
                if not stripped:
                    continue
                rec = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                       "line": stripped}
                raw_f.write(json.dumps(rec, ensure_ascii=False) + "\n")

                tag = classify(stripped)
                if tag:
                    key = (tag, stripped)
                    if key not in seen:
                        seen.add(key)
                        note({"tag": tag, "line": stripped})
                        print(f"  <{tag}> {stripped[:140]}", flush=True)
            raw_f.flush()
            tailer.save_state()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        note({"tag": "stop", "reason": "KeyboardInterrupt"})
        print("\n[live_follow] stopped by user")
    finally:
        raw_f.close()
        ev_f.close()


if __name__ == "__main__":
    main()
