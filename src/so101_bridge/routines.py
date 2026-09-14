"""Routine registry: named autonomous behaviours the controller can run.

A routine is a function ``run(ctrl)`` that only *requests* goals through the controller (``ctrl.goto``,
``ctrl.gripper_to`` …) and reports progress with ``ctrl.phase(name, detail)``. It can never bypass the
control loop's guards. Register one with::

    @routine("stack", label="Stack bricks", description="...")
    def run(ctrl): ...

Built-in routines live in their own modules and are imported here so they register on start-up.
"""

from dataclasses import dataclass
from typing import Callable

REGISTRY: dict[str, "RoutineSpec"] = {}


@dataclass(frozen=True)
class RoutineSpec:
    name: str
    label: str
    description: str
    run: Callable
    phases: tuple = ()          # expected phase sequence, for the dashboard's progress tracker


def routine(name, label=None, description="", phases=()):
    def deco(fn):
        REGISTRY[name] = RoutineSpec(name, label or name, description, fn, tuple(phases))
        return fn
    return deco


def get(name):
    _load_builtins()
    return REGISTRY.get(name)


def catalog():
    _load_builtins()
    return [{"name": r.name, "label": r.label, "description": r.description, "phases": list(r.phases)} for r in REGISTRY.values()]


def _load_builtins():
    from . import autopick  # noqa: F401  (registers "pick_place")
    from .paint import routine  # noqa: F401  (registers "paint")
