"""Chess on the arm: a discrete pick-and-place on a taught plane.

    workspace.py   config/chess.json — the taught board, pieces, slots and heights; square <-> (u, v)
    kinematics.py  GripperModel: shared.plane with the tool length fixed to the bare gripper's reach
    vision.py      overhead board reading: homography, warp, per-square occupancy and colour
    game.py        python-chess wrapper: occupancy diff -> move, legality, SAN/PGN
    players.py     who chooses the robot's move: an engine, a script, a human, or the LLM
    physical.py    move -> pick/place actions -> guarded paths
    log.py         var/games/<id>.json, the ~300-byte document the LLM reads
    routine.py     chess_setup / chess_play / chess_puzzle

The LLM never sees a frame and never applies a rule: it reads the game document and writes one move.
Everything else is deterministic Python.
"""
