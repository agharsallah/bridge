"""The game document: ``var/games/<id>.json``, and the PGN mirror beside it.

This file is the whole interface between a running game and anything outside the daemon — the
dashboard reads it, and so does the LLM when it is the one to move. It is kept small on purpose: a
FEN, whose turn it is, what the game is waiting for, and one line per move. A twenty-move game is
about 2 kB, so reading it costs a few hundred tokens and no frame ever has to be looked at.

    g = GameLog.new(mode="play", robot="white", players={"white": "llm", "black": "human"})
    g.move(by="llm", uci="e2e4", san="e4", fen_after=..., actions=[...], executed_by="robot")
    g.waiting("llm")            # /state.chess.waiting_for — what the LLM polls with `chess.sh wait`
    g.need("unreachable", move="e2e4", actions=[...])   # the only channel for asking a human for help
"""

import json
import time

from ..paths import GAMES_DIR
from ..shared.store import safe_name
from ..util import atomic_write, log
from . import game as rules

NEEDS = ("ambiguous", "promotion", "verify_failed", "unreachable", "illegal_move", "grasp_failed",
         "off_book", "feed")


class GameLog:
    def __init__(self, doc):
        self.doc = doc

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def new(cls, mode="play", robot="white", players=None, start_fen=None, name=None):
        board = rules.new_board(start_fen)
        gid = safe_name(name or time.strftime("%Y%m%d_%H%M%S"), fallback="game")
        doc = {"id": gid, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode, "robot": robot,
               "players": players or {"white": "human", "black": "human"},
               "start_fen": board.fen(), "fen": board.fen(), "turn": rules.side_name(board.turn),
               "waiting_for": None, "phase": "new", "moves": [], "result": None, "needs": None, "pgn": ""}
        g = cls(doc); g.save()
        log(f"chess: game {gid} ({mode}, robot plays {robot}, {doc['players']})")
        return g

    @classmethod
    def load(cls, gid):
        path = GAMES_DIR / f"{safe_name(gid, fallback='game')}.json"
        if not path.is_file(): return None
        return cls(json.loads(path.read_text()))

    @classmethod
    def latest(cls):
        if not GAMES_DIR.is_dir(): return None
        files = sorted(GAMES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        return cls(json.loads(files[-1].read_text())) if files else None

    @classmethod
    def catalog(cls):
        out = []
        if GAMES_DIR.is_dir():
            for p in sorted(GAMES_DIR.glob("*.json")):
                try:
                    d = json.loads(p.read_text())
                    out.append({"id": d["id"], "created": d.get("created"), "mode": d.get("mode"),
                                "robot": d.get("robot"), "moves": len(d.get("moves", [])), "result": d.get("result")})
                except Exception as e:
                    out.append({"id": p.stem, "error": str(e)})
        return out

    @property
    def id(self):
        return self.doc["id"]

    def board(self):
        return rules.new_board(self.doc["fen"])

    def save(self):
        GAMES_DIR.mkdir(parents=True, exist_ok=True)
        ucis = [m["uci"] for m in self.doc["moves"]]
        try:
            self.doc["pgn"] = rules.pgn(self.doc["start_fen"], ucis,
                                        {"Event": f"SO-101 {self.doc['mode']}", "Site": "bridge",
                                         "Date": self.doc.get("created", "")[:10].replace("-", "."),
                                         "White": self.doc["players"].get("white", "?"),
                                         "Black": self.doc["players"].get("black", "?")})
        except Exception as e:                       # a broken PGN must never stop a game being recorded
            self.doc["pgn"] = f"(pgn unavailable: {e})"
        # atomic: the LLM's `chess.sh wait` and the dashboard read this file while a game is being played,
        # and half a document parses as a crash rather than as "not ready yet"
        atomic_write(GAMES_DIR / f"{self.doc['id']}.json", json.dumps(self.doc, indent=1).encode())
        atomic_write(GAMES_DIR / f"{self.doc['id']}.pgn", (self.doc["pgn"] + "\n").encode())
        return self.doc

    # ------------------------------------------------------------------ recording
    def move(self, by, uci, san, fen_after, **extra):
        ply = len(self.doc["moves"]) + 1
        row = {"ply": ply, "by": by, "uci": uci, "san": san, "fen_after": fen_after,
               "time": time.strftime("%H:%M:%S"), **extra}
        self.doc["moves"].append(row)
        self.doc["fen"] = fen_after
        board = rules.new_board(fen_after)
        self.doc["turn"] = rules.side_name(board.turn)
        res = rules.result(board)
        if res: self.doc["result"] = {"reason": res[0], "score": res[1]}
        self.doc["needs"] = None
        self.save()
        log(f"chess: {self.doc['id']} ply {ply} {by} {san} ({uci})")
        return row

    def verify(self, ply, verdict):
        """Mark what the camera said about a move after the arm played it."""
        for m in self.doc["moves"]:
            if m["ply"] == ply: m["verify"] = verdict
        self.save()

    def phase(self, name, waiting_for=None):
        self.doc["phase"] = name
        self.doc["waiting_for"] = waiting_for
        self.save()

    def waiting(self, who):
        self.doc["waiting_for"] = who; self.save()

    def need(self, kind, **detail):
        """Ask for help. ``kind`` is one of :data:`NEEDS`; the routine holds until it is answered."""
        if kind not in NEEDS: raise ValueError(f"unknown need {kind!r} (known: {NEEDS})")
        self.doc["needs"] = {"kind": kind, "since": time.strftime("%H:%M:%S"), **detail}
        self.save()
        log(f"chess: {self.doc['id']} NEEDS {kind}: {detail}")
        return self.doc["needs"]

    def clear_need(self):
        self.doc["needs"] = None; self.save()

    def finish(self, reason=None, score=None):
        if reason: self.doc["result"] = {"reason": reason, "score": score}
        self.doc["phase"], self.doc["waiting_for"] = "finished", None
        self.save()
        log(f"chess: {self.doc['id']} finished {self.doc['result']}")

    # ------------------------------------------------------------------ what /state.chess shows
    def summary(self):
        last = self.doc["moves"][-1] if self.doc["moves"] else None
        return {"id": self.doc["id"], "mode": self.doc["mode"], "robot": self.doc["robot"],
                "players": self.doc["players"], "fen": self.doc["fen"], "turn": self.doc["turn"],
                "waiting_for": self.doc["waiting_for"], "phase": self.doc["phase"],
                "ply": len(self.doc["moves"]),
                "last_move": {k: last[k] for k in ("ply", "by", "uci", "san") } if last else None,
                "needs": self.doc["needs"], "result": self.doc["result"]}
