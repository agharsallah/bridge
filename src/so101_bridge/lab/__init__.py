"""Labs: a template for trying a new thing on the arm — safe by construction, generic, extensible.

A lab is a name, a config file (``config/<name>.json``), a set of actions and a page. It brings:

* the control loop's guards, untouched: every move is a request (step cap, soft limits, table guard,
  load/stall guards, ESTOP) — a lab cannot bypass them;
* named points and a lab rest, recorded from the present pose, with a lab-only joint *envelope* for
  points beyond the soft limits (capped by the motor sweep; no register or shared config is written);
* staged changes: edits are pending until applied, visible, editable and discardable;
* raw camera transport (no vision) for the page and for models, and recording into a configured folder;
* a self-describing action API (``/<lab>/actions``) and a compact observation (``/<lab>/observe``) for
  models and LLM tools.

Start a new lab with ``make lab NAME=<name>`` (docs/lab.md is the full guide)::

    # src/so101_bridge/labs/<name>.py  — found and registered automatically at start-up
    from so101_bridge.lab import BUILTIN, Lab, register
    LAB = register(Lab("<name>", "<Title>", actions=BUILTIN, ui="panels.js", defaults={...}))

    @LAB.action("<verb>", "<Label>", "<what it does>", params={"x": "number"}, moves=True)
    def verb(lab, ctrl, args): ...
"""

import re

from .actions import BUILTIN
from .core import Action, Lab

LABS: dict[str, Lab] = {}


RESERVED = {"", "static", "labs", "state", "cmd", "log", "videos", "waypoints", "paint", "chess", "init", "top.mjpg", "wrist.mjpg"}


def register(lab: Lab) -> Lab:
    """Make ``lab`` reachable at /<name>. Names are lowercase [a-z0-9_] and must not shadow a dashboard route."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", lab.name) or lab.name in RESERVED:
        raise ValueError(f"lab name {lab.name!r}: use lowercase letters, digits and _, not one of the dashboard's routes")
    if lab.name in LABS and LABS[lab.name] is not lab:
        raise ValueError(f"lab {lab.name!r} is registered twice")
    LABS[lab.name] = lab
    return lab


LAB = register(Lab("lab", "Lab", actions=BUILTIN, description="the template: points, rest, envelope, tool, cameras, video"))


def discover():
    """Import every module in ``so101_bridge.labs`` so its ``register(...)`` runs. Broken ones are logged, not fatal."""
    import importlib
    import pkgutil

    from .. import labs as pkg
    from ..util import log
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("_"): continue
        try: importlib.import_module(f"{pkg.__name__}.{m.name}")
        except Exception as e: log(f"LAB {m.name}: not loaded ({type(e).__name__}: {e})")
    return LABS


__all__ = ["Action", "BUILTIN", "LAB", "LABS", "Lab", "discover", "register"]
