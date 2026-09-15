"""Who chooses the robot's move.

The arm does not care where a move comes from, so the choice is a one-method object:

    Player.choose(board, glog, ctrl) -> uci string, or None meaning "not mine to make"

``HumanPlayer`` returns None — for that side the routine watches the board instead of asking anyone.
``LlmPlayer`` parks the game in ``waiting_for: "llm"`` and blocks until a ``chess_move`` command
arrives; that is the entire LLM interface, and it costs one small JSON read and one command per move.
``EnginePlayer`` needs a UCI engine on disk (``CHESS_ENGINE``, e.g. ``brew install stockfish``);
without one it is never constructed. ``ScriptPlayer`` replays a puzzle line and hands back control the
moment the position leaves the script.
"""

import time

from ..settings import CHESS_ENGINE, CHESS_ENGINE_LIMIT, CHESS_MOVE_TIMEOUT_S
from ..util import log
from . import game as rules


class Player:
    kind = "player"

    def choose(self, board, glog, ctrl):
        raise NotImplementedError

    def close(self):
        pass


class HumanPlayer(Player):
    """A person moves the pieces themselves; the camera reports what they did."""
    kind = "human"

    def choose(self, board, glog, ctrl):
        return None


class LlmPlayer(Player):
    """Blocks until a ``chess_move`` command arrives. Illegal moves are logged and it keeps waiting."""
    kind = "llm"

    def __init__(self, timeout_s=None):
        self.timeout_s = CHESS_MOVE_TIMEOUT_S if timeout_s is None else float(timeout_s)

    def choose(self, board, glog, ctrl):
        glog.phase("robot_turn", waiting_for="llm")
        t0 = time.time()
        while time.time() - t0 < self.timeout_s:
            if ctrl.routine_abort.is_set(): return None
            req = getattr(ctrl, "chess_request", None) or {}
            uci = req.get("move")
            if uci:
                ctrl.chess_request = {k: v for k, v in req.items() if k != "move"}
                uci = str(uci).strip().lower()
                if rules.is_legal(board, uci):
                    glog.clear_need()
                    return uci
                log(f"chess: rejected illegal move {uci!r} from the LLM")
                glog.need("illegal_move", move=uci, fen=board.fen(),
                          hint="send a legal UCI move, e.g. e2e4 (promotion: e7e8q)")
            time.sleep(0.1)
        log(f"chess: no move from the LLM in {self.timeout_s:.0f} s")
        return None


class ScriptPlayer(Player):
    """Plays a fixed line (a puzzle solution). Defers as soon as the game leaves the script."""
    kind = "script"

    def __init__(self, line):
        self.line = [str(u).strip().lower() for u in line]
        self.i = 0

    def choose(self, board, glog, ctrl):
        while self.i < len(self.line):
            uci = self.line[self.i]; self.i += 1
            if rules.is_legal(board, uci):
                return uci
            log(f"chess: puzzle line left the book at {uci!r}")
            glog.need("off_book", move=uci, fen=board.fen())
            return None
        glog.need("off_book", move=None, fen=board.fen(), hint="the scripted line is finished")
        return None


class EnginePlayer(Player):
    """A UCI engine (Stockfish and friends). Constructed only when ``CHESS_ENGINE`` points at one."""
    kind = "engine"

    def __init__(self, path=None, limit_s=None):
        import chess.engine
        self.path = path or CHESS_ENGINE
        if not self.path:
            raise RuntimeError("no engine configured — set CHESS_ENGINE in config/settings.json")
        self.limit = chess.engine.Limit(time=float(limit_s if limit_s is not None else CHESS_ENGINE_LIMIT))
        self.engine = chess.engine.SimpleEngine.popen_uci(self.path)
        log(f"chess: engine {self.path} ready")

    def choose(self, board, glog, ctrl):
        glog.phase("robot_turn", waiting_for="engine")
        res = self.engine.play(board, self.limit)
        return res.move.uci() if res.move else None

    def close(self):
        try: self.engine.quit()
        except Exception: pass


KINDS = {"human": HumanPlayer, "llm": LlmPlayer, "engine": EnginePlayer, "script": ScriptPlayer}


def make(kind, **kw):
    """Build a player by name, falling back to the LLM when an engine was asked for but is not installed."""
    kind = (kind or "human").lower()
    if kind not in KINDS: raise ValueError(f"unknown player {kind!r} (known: {sorted(KINDS)})")
    if kind == "engine" and not (kw.get("path") or CHESS_ENGINE):
        log("chess: no CHESS_ENGINE configured — that side will wait for chess_move instead")
        return LlmPlayer()
    return KINDS[kind](**kw)
