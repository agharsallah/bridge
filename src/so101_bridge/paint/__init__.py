"""Painting: teach a paper and paint stations, turn a picture into brush strokes, compile them into a replayable
joint-space program, and run it through the guarded control loop.

    workspace.py   config/painting.json — paper size, taught corner poses, stations (colours, water, towel), brush params
    kinematics.py  ToolModel: brush-tip kinematics on the paper plane fitted from the taught corners (IK + reachability)
    planner.py     picture -> colour-quantised hatch strokes in paper coordinates (cm)
    program.py     strokes -> program of guarded 'path' steps with precomputed joint poses; save / load / list
    routine.py     'paint' and 'paint_dry_run' routines that execute a saved program
"""
