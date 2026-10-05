"""From tmp/live_follow_events.jsonl, reconstruct per-duel records.

Pairs COIN -> DUEL_START -> TURN1 and appends GAME_ENDED/UPLOAD.
Prints the coin-vs-turn-order cross-tab.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EV = os.path.join(ROOT, "tmp", "live_follow_events.jsonl")

TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\]")
TURN1 = re.compile(r"Turn 1 for player (\d)")
DECKID = re.compile(r"deckId=(\S+?),")
RESULT = re.compile(r"result=(\w+)")

duels = []
cur = None

for raw in open(EV, encoding="utf-8"):
    raw = raw.strip()
    if not raw:
        continue
    rec = json.loads(raw)
    tag = rec.get("tag")
    line = rec.get("line", "")
    m = TS.match(line)
    when = m.group(1) if m else "?"

    if tag == "COIN":
        # Close any dangling duel first (no end marker seen).
        if cur is not None:
            cur["orphan"] = True
            duels.append(cur)
        who = "Player" if "Player is deciding" in line else "Opponent"
        cur = {"coin_ts": when, "coin_winner": who}
    elif tag == "DUEL_START" and cur is not None:
        cur["start_ts"] = when
        dm = DECKID.search(line)
        cur["deck_id"] = dm.group(1) if dm else None
    elif tag == "TURN1" and cur is not None and "went_first" not in cur:
        tm = TURN1.search(line)
        if tm:
            cur["turn1_ts"] = when
            cur["first_player"] = int(tm.group(1))
    elif tag == "GAME_ENDED" and cur is not None:
        rm = RESULT.search(line)
        cur["result"] = rm.group(1) if rm else None
        cur["end_ts"] = when
    elif tag == "UPLOAD" and cur is not None:
        duels.append(cur)
        cur = None

if cur is not None:
    cur["orphan"] = True
    duels.append(cur)

print(f"共解析出 {len(duels)} 个对局段（含未结束）\n")
print(f"{'#':>3} {'硬币获胜':<8} {'实际先手':<9} {'deckId':<12} {'结果':<6} {'轮转?':<5}")
print("-" * 60)
for i, d in enumerate(duels, 1):
    coin = d.get("coin_winner", "?")
    fp = d.get("first_player")
    if fp is None:
        order = "未到T1"
    else:
        order = "我" if fp == 0 else "对方"
    deck = str(d.get("deck_id", "?"))[:11]
    res = d.get("result", "无结束")
    orph = "★孤儿" if d.get("orphan") else ""
    print(f"{i:>3} {coin:<8} {order:<9} {deck:<12} {res:<6} {orph}")

# Cross-tab using only complete duels
complete = [d for d in duels if d.get("coin_winner") and d.get("first_player") is not None]
print(f"\n完整数据 {len(complete)} 局：")
both_ok = both_bad = coin_me_second = coin_opp_first = 0
for d in complete:
    coin_me = d["coin_winner"] == "Player"
    first_me = d["first_player"] == 0
    if coin_me and first_me:
        both_ok += 1
    elif not coin_me and not first_me:
        both_bad += 1
    elif coin_me and not first_me:
        coin_me_second += 1
    else:
        coin_opp_first += 1

print(f"  赢硬币且先手         : {both_ok}")
print(f"  输硬币且后手         : {both_bad}")
print(f"  赢硬币但选择后手 ★   : {coin_me_second}")
print(f"  输硬币但成为先手 ★   : {coin_opp_first}")
