"""Brush-tip kinematics on the paper plane — the painting-shaped view of :mod:`shared.plane`.

All the geometry (tool-length fit, homography, lean window, IK, reachability) lives in
``shared/plane.py`` and is shared with chess. This module only knows what painting knows: where the
taught contacts come from (``config/painting.json``), that the paper is A5-ish, and that the tool is a
brush glued into the gripper whose length nobody measured — so it is fitted rather than given.

    tool = ToolModel(ctrl.floor, workspace.load(), limits)
    tool.ok                      # fitted?
    tool.uv_to_pose(u, v, z_cm)  # joint dict for the brush tip at (u, v) cm, z above the paper — or None
    tool.reachability(step_cm)   # grid mask of the paper
"""

from ..shared.motion import pose_with_gripper  # noqa: F401  (re-exported: callers used to import it from here)
from ..shared.plane import (  # noqa: F401  (re-exported: the tuning constants were public here)
    LEAN_MAX_DEV_DEG,
    LEAN_STEP_DEG,
    PLANE_WEIGHT,
    REACH_MARGIN_M,
    TOOL_DEFAULT_M,
    TOOL_MAX_M,
    TOOL_MIN_M,
    PlaneModel,
)
from ..shared.plane import SIZE_FIT_WARN_CM as PAPER_FIT_WARN_CM  # noqa: F401
from .workspace import CORNERS, contact_points, corner_uv

TERMS = {"tool": "brush", "plane": "paper", "depths": "hover and press depth",
         "touching": "the brush touching the paper",
         "orient": "looking at the paper the way the picture will be oriented"}


class ToolModel(PlaneModel):
    """A :class:`PlaneModel` whose contacts, size and corner cycle come from ``config/painting.json``."""

    def __init__(self, floor, ws, limits=None):
        self.ws = ws
        paper = ws["paper"]
        size = (float(paper["width_cm"]), float(paper["height_cm"]))
        cs = paper.get("corners", {})
        quad = [(n, *corner_uv(ws, n), cs[n]) for n in CORNERS] if all(n in cs for n in CORNERS) else None
        super().__init__(floor, contact_points(ws), size, limits=limits, tool_len=None, quad=quad, terms=TERMS)

    @property
    def z_paper(self):
        """Height of the taught paper plane in the arm frame (m). Named for the painting code that predates shared/."""
        return self.z_plane
