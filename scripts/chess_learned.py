#!/usr/bin/env python3
"""Print what var/chess_history.jsonl has learned, and what it would suggest changing.

    scripts/chess.sh learned          # this script
    scripts/chess.sh apply <id>       # apply one of the suggestions it prints

Read-only: it never writes to config/chess.json. Applying is a separate, deliberate command.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from so101_bridge.chess import history as H  # noqa: E402


def main():
    st = H.stats()
    t = st["totals"]
    if not t["rows"]:
        print(f"nothing recorded yet — {H.HISTORY_FILE} is empty.")
        print("Play a game (scripts/chess.sh play) and the grasps, places and verifications land here.")
        return 0
    print(f"{t['rows']} records, {t['games']} games, {t['moves']} moves "
          f"({t['by_human']} played by a human), {t['verify_fail']} verify failures")
    if t["grasps"]:
        print(f"grasps: {t['grasps'] - t['misses']}/{t['grasps']} held ({t['grasp_success_pct']}%)")

    rows = [(pc["name"], pc) for _, pc in st["pieces"].items() if pc["grasps"]]
    if rows:
        print("\nby piece:")
        print(f"  {'piece':<8}{'held':>9}  {'taught z':>9}  {'settles':>8}  {'threshold':>9}")
        for name, pc in sorted(rows, key=lambda kv: -kv[1]["grasps"]):
            print(f"  {name:<8}{pc['grasps'] - pc['misses']:>4}/{pc['grasps']:<4}"
                  f"  {str(pc['taught_z_cm']):>9}  {str(pc['mean_held']):>8}  {str(pc['stall_min']):>9}")

    bad = [(s, r) for s, r in sorted(st["squares"].items()) if r["misses"] + r["verify_fail"]]
    if bad:
        print("\nsquares that went wrong:")
        for s, r in bad:
            if r["attempts"]:
                what = f"{r['misses']} missed and {r['verify_fail']} verify failures of {r['attempts']} attempts"
            else:
                what = f"{r['verify_fail']} verify failure(s) — nothing was ever picked or placed there"
            print(f"  {s}: {what}"
                  + (f", mean lean {r['mean_lean']} deg" if r["mean_lean"] is not None else ""))

    sug = H.suggestions(st)
    print(f"\n{len(sug)} suggestion(s):" if sug else
          "\nno suggestions — there is not enough evidence yet "
          f"(a conclusion needs at least {H.MIN_ATTEMPTS} attempts at the same thing)")
    for x in sug:
        print(f"\n  [{x['id']}] {x['what']}")
        print(f"      why: {x['why']}")
        if x["path"]:
            print(f"      {x['path']}: {x['current']} -> {x['value']}")
            print(f"      apply with: scripts/chess.sh apply {x['id']}")
        else:
            print("      needs a person — there is no automatic form of this change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
