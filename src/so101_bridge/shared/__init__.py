"""Generic workspace machinery shared by every task the arm performs.

``paint/`` owned all of this first; chess needed the same plane fitting, the same guarded-path helpers
and the same JSON store, so the parts that are not about brushes or pictures live here. Nothing in
``shared/`` knows what a painting or a chess board is.

    plane.py   PlaneModel  — fingertip/tool kinematics on a taught flat surface
    motion.py  densify, lift, bridge, plane_travel, probe-based z correction
    store.py   tiny JSON helpers for the config files the operator teaches into
    teach.py   guided teaching sessions ("put the tip on a1", operator drives, presses Done)
"""
