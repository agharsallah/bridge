"""Pick a camera by model name and position, so the laptop's own camera or a virtual one is never opened.

OpenCV opens cameras by index (on macOS: AVFoundation's device order). A plain index is fragile: when the
wrist camera drops off the bus the next device slides into its slot, and the laptop camera appeared as
"wrist". Device unique IDs do not help here either: both Innomaker cameras report the same USB serial
(SN0001), and AVFoundation hands their IDs out inconsistently — the same ID opened the wrist camera in one
process and the tower camera in the next (measured 2026-11-12). The OpenCV order of the two, however, was
stable.

So ``settings.CAMS`` names a camera as ``"<model>#<n>"``: the n-th device (0-based, in OpenCV order) whose
name contains ``<model>``, e.g. ``{"top": "Innomaker#1", "wrist": "Innomaker#0"}``. Other devices are never
counted, so they can never be picked; if fewer such cameras are connected the camera is reported missing.
After re-plugging, the two identical cameras may come back in the other order: swap them from the lab's
camera panel (or in config/settings.json). A plain int is still used as a raw index.
``python -m so101_bridge.camera_ids`` lists what is connected.
"""

import sys


def devices():
    """[(index, unique_id, name)] in OpenCV's order (video devices, then muxed), or [] when unavailable."""
    try:
        import AVFoundation as A
    except ImportError:
        return []
    found = list(A.AVCaptureDevice.devicesWithMediaType_(A.AVMediaTypeVideo))
    found += list(A.AVCaptureDevice.devicesWithMediaType_(A.AVMediaTypeMuxed))
    return [(i, str(d.uniqueID()), str(d.localizedName())) for i, d in enumerate(found)]


def resolve(selector, listing=None):
    """The OpenCV index for ``selector``: an int is used as is; ``"model#n"`` is the n-th device of that model.

    Returns None when there is no such device (or the devices cannot be listed): the caller opens nothing.
    """
    if isinstance(selector, int): return selector
    model, _, n = str(selector).rpartition("#")
    if not model or not n.isdigit(): raise ValueError(f"camera {selector!r}: expected an int or 'model#n'")
    same = [i for i, _, name in (devices() if listing is None else listing) if model.lower() in name.lower()]
    return same[int(n)] if int(n) < len(same) else None


if __name__ == "__main__":
    rows = devices()
    if not rows: sys.exit("no camera listing (pyobjc-framework-AVFoundation missing, or not macOS)")
    for i, uid, name in rows: print(f"{i}  {name:32s}  {uid}")
