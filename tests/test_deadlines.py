"""
Comptes à rebours : le JavaScript de la page ET le Python de l'e-mail doivent
donner exactement le même « J-n ».

Le JS est exécuté tel quel, extrait de webui.html, dans Node ou dans le
navigateur de Playwright — pas une réécriture en Python, sinon le test ne
prouverait rien sur ce qui tourne réellement.

Régression d'origine (5 septembre 2026, 03h03) :
  échéance 2026-09-07 09:00  affichait « J-3 »        au lieu de « J-2 »
  échéance 2026-09-04 09:00  affichait « aujourd'hui » au lieu de « dépassé »
La première venait d'un Math.ceil sur une durée fractionnaire, la seconde
du -0 de JavaScript, pour lequel `-0 < 0` est faux.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuneps.report import _days_left  # noqa: E402

WEBUI = Path(__file__).resolve().parent.parent / "tuneps" / "webui.html"

# (maintenant, échéance, attendu)   -1 = dépassé, 0 = aujourd'hui, n = J-n
CASES: list[tuple[str, str, int | None]] = [
    # --- les deux cas signalés
    ("2026-09-05 03:03", "2026-09-07 09:00:00", 2),
    ("2026-09-05 03:03", "2026-09-04 09:00:00", -1),
    # --- aujourd'hui, avant et après l'heure limite
    ("2026-09-05 03:03", "2026-09-05 09:00:00", 0),
    ("2026-09-05 15:00", "2026-09-05 09:00:00", -1),
    # --- demain, quelle que soit l'heure qu'il est
    ("2026-09-05 03:03", "2026-09-06 01:00:00", 1),
    ("2026-09-05 23:59", "2026-09-06 08:00:00", 1),
    ("2026-09-05 00:01", "2026-09-06 23:00:00", 1),
    # --- franchissements de mois et d'année
    ("2026-08-31 22:00", "2026-09-01 09:00:00", 1),
    ("2026-12-31 20:00", "2027-01-02 09:00:00", 2),
    ("2026-02-27 10:00", "2026-03-01 09:00:00", 2),
    # --- échéances lointaines
    ("2026-09-05 03:03", "2026-10-05 09:00:00", 30),
    ("2026-09-05 03:03", "2027-09-05 09:00:00", 365),
    # --- entrées dégradées
    ("2026-09-05 03:03", "", None),
    ("2026-09-05 03:03", "pas une date", None),
]


def extract_js() -> str:
    """Récupère daysLeft() depuis la page, sans la réécrire."""
    html = WEBUI.read_text(encoding="utf-8")
    m = re.search(r"function daysLeft\(dl, now\)\{.*?\n\}", html, re.S)
    if not m:
        raise AssertionError("daysLeft(dl, now) introuvable dans webui.html — "
                             "la signature a changé, le test doit suivre")
    return m.group(0)


def run_js(cases) -> list:
    """Exécute le vrai JS. Node si disponible, sinon Chromium via Playwright."""
    script = extract_js() + """
const cases = %s;
const out = cases.map(([now, dl]) => {
  const m = /^(\\d{4})-(\\d{2})-(\\d{2}) (\\d{2}):(\\d{2})$/.exec(now);
  const ref = new Date(+m[1], +m[2]-1, +m[3], +m[4], +m[5]);
  return daysLeft(dl, ref);
});
""" % json.dumps([[c[0], c[1]] for c in cases])

    if shutil.which("node"):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script + "console.log(JSON.stringify(out));")
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                return json.loads(r.stdout.strip())
        finally:
            Path(path).unlink(missing_ok=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        res = pg.evaluate("() => { " + script + " return out; }")
        b.close()
    return res


def run() -> int:
    fails: list[str] = []

    # --- côté navigateur
    try:
        got = run_js(CASES)
    except Exception as e:  # noqa: BLE001
        print(f"comptes à rebours : impossible d'exécuter le JS ({e})")
        return 1

    for (now, dl, want), g in zip(CASES, got):
        if g != want:
            fails.append(f"JS  {now} → « {dl} » : obtenu {g}, attendu {want}")
        if g == 0 and str(g) == "-0":
            fails.append(f"JS  {now} → « {dl} » : -0 au lieu de 0")

    # --- côté e-mail : mêmes réponses, à la même seconde
    import tuneps.report as report

    class FrozenDatetime(dt.datetime):
        frozen = dt.datetime(2026, 9, 5, 3, 3)

        @classmethod
        def now(cls, tz=None):
            return cls.frozen

    real = report.dt.datetime
    try:
        report.dt.datetime = FrozenDatetime
        for now, dl, want in CASES:
            FrozenDatetime.frozen = dt.datetime.strptime(now, "%Y-%m-%d %H:%M")
            g = _days_left(dl)
            if g != want:
                fails.append(f"e-mail {now} → « {dl} » : obtenu {g}, attendu {want}")
    finally:
        report.dt.datetime = real

    print(f"comptes à rebours : {len(CASES) * 2 - len(fails)}/{len(CASES) * 2} "
          f"(page + e-mail)")
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
