"""lerobot's own calibration material, read from the installed checkout so the page never drifts from it."""

import re
from pathlib import Path

from ..settings import PORT, ROBOT_ID

VIDEO = "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/lerobot/calibrate_so101_2.mp4"


def _section(text: str, heading: str) -> str:
    """The markdown section under ``heading`` (up to the next heading of the same level), MDX tags stripped."""
    level = heading.split()[0]
    m = re.search(rf"^{re.escape(heading)}\s*$(.*?)(?=^{level} |\Z)", text, re.S | re.M)
    if not m: return ""
    body = re.sub(r"<(/?)(hfoptions|hfoption|div|video|source)[^>]*>|<!--.*?-->", "", m.group(1), flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def lerobot_docs() -> dict:
    """Version, source path, the SO-101 'Calibrate' doc section, the homing diagram and the CLI command."""
    out = {"version": None, "path": None, "doc": "", "diagram": "", "video": VIDEO,
           "cli": f"uv run lerobot-calibrate --robot.type=so101_follower --robot.port={PORT} --robot.id={ROBOT_ID}"}
    try:
        import lerobot
        from lerobot.robots.so_follower import so_follower
    except ImportError as e:
        out["error"] = f"lerobot not importable: {e}"; return out
    out["version"] = getattr(lerobot, "__version__", None)
    out["path"] = str(Path(lerobot.__file__).parent)
    out["diagram"] = getattr(so_follower, "_HOMING_POSITION_DIAGRAM", "")
    mdx = Path(lerobot.__file__).parents[2] / "docs" / "source" / "so101.mdx"
    if mdx.is_file():
        out["doc"] = _section(mdx.read_text(), "## Calibrate").split("#### Leader")[0].strip()   # follower only
        out["doc_file"] = str(mdx)
    return out
