"""Square Kufic for the drawing lab: words designed on a unit grid (strokes and gaps exactly one unit wide, only
horizontal and vertical lines), drawn as the outline of the letters. Each attached group of letters is one closed
line; holes and dots are small squares. Contours come out right to left, top line first — the way it is written."""

# جمعة مباركة — top line جمعة, bottom line مباركة. '#' = ink. Rightmost column is where each word starts.
#   جمعة (16 wide, shifted 2 in): ة box + 2 dots, ع raised box on a stem, م box, ج bracket open to the left + dot below.
#   مباركة (20 wide): ة box + 2 dots, ك tall bracket with a flag, ر descending hook, ا tall, ب tooth + dot below, م box.
JUMUA = """
..#.#.###...........
......#.#...........
..###.###.###.####..
..#.#..#..#.#....#..
..################..
....................
....#........#.#....
#.#.####.....#......
.......#.....#......
###....#...#.#.#.###
#.#....#...#.#.#.#.#
########...#.#######
...........#........
.........###...#....
"""


def outlines(art):
    """The contours of a '#' bitmap as closed strokes [[x, y], ...] in grid units (y up), corners only."""
    rows = [r for r in art.split("\n") if r.strip()]
    cells = {(x, len(rows) - 1 - y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == "#"}
    edges = set()
    for x, y in cells:                                        # counter-clockwise around each cell; shared edges cancel
        for a, b in (((x, y), (x + 1, y)), ((x + 1, y), (x + 1, y + 1)), ((x + 1, y + 1), (x, y + 1)), ((x, y + 1), (x, y))):
            if (b, a) in edges: edges.remove((b, a))
            else: edges.add((a, b))
    nxt = {}
    for a, b in edges: nxt.setdefault(a, []).append(b)
    loops = []
    while nxt:
        start = next(iter(nxt)); loop, p = [start], start
        while True:
            q = nxt[p].pop()
            if not nxt[p]: del nxt[p]
            if q == start: break
            loop.append(q); p = q
        loop = [p for i, p in enumerate(loop)                 # keep corners only
                if (p[0] - loop[i - 1][0]) * (loop[(i + 1) % len(loop)][1] - p[1]) != (p[1] - loop[i - 1][1]) * (loop[(i + 1) % len(loop)][0] - p[0])]
        k = max(range(len(loop)), key=lambda i: (loop[i][1], -loop[i][0]))      # start at a top corner: the pen lands along a vertical edge
        loops.append([[float(v) for v in p] for p in loop[k:] + loop[:k + 1]])
    return loops


def jumua():
    """جمعة مباركة with a line under it. Top line first, each line right to left."""
    loops = outlines(JUMUA)
    loops.sort(key=lambda s: (min(p[1] for p in s) < 7, -max(p[0] for p in s)))      # rows 7 and up are the top line
    return loops + [[[20.3, -1.2], [-0.3, -1.2]]]
