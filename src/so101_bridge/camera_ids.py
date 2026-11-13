"""Pick a camera by model name and position, so the laptop's own camera or a virtual one is never opened.

OpenCV opens cameras by index; on macOS that is the AVFoundation device list *sorted by uniqueID*
(cap_avfoundation_mac.mm). A plain index is fragile: when a camera drops off the bus, or the virtual OBS
camera sorts in between, another device slides into its slot (the laptop and OBS cameras were shown as
"top"/"wrist").

So ``settings.CAMS`` names a camera as ``"<model>#<n>"``: the n-th device of that model in OpenCV order,
e.g. ``{"top": "Innomaker#1", "wrist": "Innomaker#0"}``. Other devices are never counted, so they can never
be picked; if fewer such cameras are connected the camera is reported missing. A uniqueID encodes the USB
port, and the two Innomakers share one serial, so after moving them to other ports they may come back in
the other order: camera_identity then tells them apart by their view and swaps them. A plain int is still
used as a raw index.
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
    found.sort(key=lambda d: str(d.uniqueID()))      # OpenCV sorts by uniqueID before indexing: same order here
    return [(i, str(d.uniqueID()), str(d.localizedName())) for i, d in enumerate(found)]


def pick(selector, listing=None, exclude=(), prefer=""):
    """``(index, uid)`` for ``selector``, never a device in ``exclude`` (uids other cameras use), or None.

    ``prefer``: the uid this camera had before a drop; if that device is back (and free), it is taken again.

    An int is a raw index. ``"model#n"`` is the n-th device of that model; if that one is taken or missing,
    the first free device of the model is used instead (with one twin unplugged the positions shift, and two
    cameras must never end up on the same device). The identity check then sorts out which twin is which.
    """
    rows = devices() if listing is None else listing
    if isinstance(selector, int):
        return next(((i, uid) for i, uid, _ in rows if i == selector), (selector, ""))
    model, _, n = str(selector).rpartition("#")
    if not model or not n.isdigit(): raise ValueError(f"camera {selector!r}: expected an int or 'model#n'")
    same = [(i, uid) for i, uid, name in rows if model.lower() in name.lower()]
    back = next((d for d in same if prefer and d[1] == prefer and d[1] not in exclude), None)
    if back: return back
    if int(n) < len(same) and same[int(n)][1] not in exclude: return same[int(n)]
    return next((d for d in same if d[1] not in exclude), None)


def resolve(selector, listing=None, exclude=()):
    """The OpenCV index for ``selector`` (see :func:`pick`), or None: then the caller opens nothing."""
    found = pick(selector, listing, exclude)
    return None if found is None else found[0]


def choices(listing=None):
    """Every connected device with the selector that names it: [{index, name, uid, selector}] in OpenCV order."""
    rows, seen = (devices() if listing is None else listing), {}
    out = []
    for i, uid, name in rows:
        k = seen[name] = seen.get(name, -1) + 1
        out.append({"index": i, "name": name, "uid": uid, "selector": f"{name}#{k}"})
    return out


def listing_fresh(timeout=15):
    """``choices()`` from a fresh process: a long-running one may not see cameras plugged in after it started."""
    import json
    import subprocess
    r = subprocess.run([sys.executable, "-m", "so101_bridge.camera_ids", "--json"], capture_output=True, text=True, timeout=timeout)
    if r.returncode: raise RuntimeError(f"camera listing failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return json.loads(r.stdout)


if __name__ == "__main__":
    rows = devices()
    if "--json" in sys.argv:
        import json
        print(json.dumps(choices(rows))); sys.exit(0)
    if not rows: sys.exit("no camera listing (pyobjc-framework-AVFoundation missing, or not macOS)")
    for c in choices(rows): print(f"{c['index']}  {c['name']:32s}  {c['uid']:40s}  {c['selector']}")
