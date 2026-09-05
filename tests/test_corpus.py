"""Évaluation du moteur sur le corpus réel (tests/corpus.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.corpus import CORPUS  # noqa: E402
from tuneps.matcher import default_matcher  # noqa: E402

M = default_matcher()


def run(verbose: bool = True) -> int:
    errs: list[str] = []
    stats = {"yes": [0, 0], "maybe": [0, 0], "no": [0, 0]}

    for text, label in CORPUS:
        r = M.match(text)
        stats[label][1] += 1
        ok = True
        if label == "yes":
            ok = r.matched and r.confidence == "high"
        elif label == "maybe":
            ok = r.matched          # forte ou à vérifier : les deux conviennent
        else:                       # "no"
            ok = not r.matched
        if ok:
            stats[label][0] += 1
        else:
            got = r.confidence if r.matched else ("exclu:" + (r.excluded_by or "-") if r.excluded_by else "aucun")
            errs.append(f"[attendu {label:5} / obtenu {got:8}] {text[:95]}"
                        + (f"\n        -> {r.summary()}" if r.hits else ""))

    total_ok = sum(v[0] for v in stats.values())
    total = sum(v[1] for v in stats.values())
    if verbose:
        for k, (ok_n, n) in stats.items():
            print(f"  {k:6} : {ok_n}/{n}")
        print(f"  TOTAL  : {total_ok}/{total}")
        for e in errs:
            print("  ✗", e)
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(run())
