"""Exhaustive chunking-invariance check for app/core/style.py (about 1 minute).

.venv/bin/python scripts/check_style_chunking.py
"""

import itertools
import sys

sys.path.insert(0, ".")
from app.core.style import StyleGuard, apply_style


def feed_all(chunks):
    g = StyleGuard()
    return "".join(g.feed(c) for c in chunks) + g.flush()


alphabet = ["a", "!", " ", ")", ".", "\n", "🙂"]
bad = 0
total = 0
for n in range(1, 7):
    for tup in itertools.product(alphabet, repeat=n):
        t = "".join(tup)
        w = apply_style(t)
        for k in range(0, 4):
            for cuts in itertools.combinations(range(1, n), k):
                pts = (0, *cuts, n)
                ch = [t[a:b] for a, b in itertools.pairwise(pts)]
                total += 1
                if feed_all(ch) != w:
                    bad += 1
                    if bad < 4:
                        print("MISMATCH", ch, repr(w), repr(feed_all(ch)))
print(
    "texts up to length 6 over a 7-symbol alphabet; chunkings checked:", total, "mismatches:", bad
)
