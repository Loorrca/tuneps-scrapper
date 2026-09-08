"""
Alimenter le jeu de données « prix marché » depuis TUNEPS.

La saisie manuelle des montants est le vrai coût du module : quarante marchés,
plusieurs concurrents chacun, des montants à recopier sans se tromper. Or le
portail publie déjà tout cela. Ce module va le chercher.

Ce qu'il récupère, et ce qu'il ne récupère pas
----------------------------------------------
Il récupère : les soumissionnaires, leur MATRICULE FISCAL, leurs montants, le
rang, le lot. Il ne récupère pas les QUANTITÉS par article — le portail ne les
publie pas sous une forme exploitable, et de toute façon c'est le
rapprochement avec vos 16 articles qui demande votre jugement. Les entrées
importées arrivent donc sans composition, et n'entrent dans aucun calcul tant
que vous ne l'avez pas renseignée. C'est le partage du travail : la machine
fait la recopie, vous faites la qualification.

Une entrée par lot
------------------
Un marché à trois lots, c'est trois compositions différentes et trois montants
par concurrent. On crée donc trois entrées. Les fusionner en additionnant les
montants serait faux dès qu'un concurrent n'a pas soumissionné sur tous les
lots : son total ne couvrirait pas la composition qu'on lui attribue, et
l'écart irait polluer l'estimation de tous ses autres marchés.

Ce que chaque source publie
---------------------------
Mesuré sur le portail en septembre 2026 :
  - appels d'offres : environ deux sur trois exposent le tableau chiffré ;
  - consultations   : la liste des soumissionnaires est presque toujours là,
    mais sans montant. La version chiffrée existe et reste rare.
On importe ce qui est chiffré, quelle que soit la source, et on ignore le
reste sans bruit.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    """Un lot prêt à être versé au jeu de données."""
    uid: str
    lot: str
    ref: str
    buyer: str
    tdate: str
    source: str
    title: str
    url: str
    bids: list[dict] = field(default_factory=list)   # {name, reg_no, amount, rank}
    already: bool = False                            # déjà importé

    def as_dict(self) -> dict:
        return {
            "uid": self.uid, "lot": self.lot, "ref": self.ref, "buyer": self.buyer,
            "tdate": self.tdate, "source": self.source, "title": self.title,
            "url": self.url, "bids": self.bids, "already": self.already,
        }


def _months(since: str, today: dt.date | None = None) -> list[tuple[int, int]]:
    """Les couples (année, mois) de `since` (AAAA-MM) jusqu'au mois courant."""
    today = today or dt.date.today()
    try:
        y, m = (int(x) for x in since.split("-")[:2])
    except Exception:  # noqa: BLE001
        y, m = today.year, today.month
    out: list[tuple[int, int]] = []
    while (y, m) <= (today.year, today.month) and len(out) < 60:
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def scan(client, matcher, results_client, known: set[tuple[str, str]],
         since: str = "2026-05", limit: int | None = None,
         progress: Callable[[str], None] | None = None) -> dict:
    """Parcourt la période et renvoie les lots chiffrés qui concernent l'activité.

    N'écrit rien : c'est délibéré. On montre d'abord ce qui serait importé.
    """
    stats = {"months": 0, "scanned": 0, "matched": 0, "checked": 0,
             "with_results": 0, "priced_lots": 0, "already": 0,
             "errors": 0, "since": since}
    out: list[Candidate] = []

    for (y, m) in _months(since):
        stats["months"] += 1
        if progress:
            progress(f"{y:04d}-{m:02d}")
        try:
            notices = client.month_window(y, m)
        except Exception as e:  # noqa: BLE001
            log.warning("Fenêtre %04d-%02d illisible : %s", y, m, e)
            stats["errors"] += 1
            continue
        stats["scanned"] += len(notices)

        for n in notices:
            res = matcher.match(*n.titles)
            if not res.matched:
                continue
            stats["matched"] += 1
            if limit is not None and stats["checked"] >= limit:
                continue
            stats["checked"] += 1
            try:
                r = results_client.for_notice(n.source, n.number, n.mod_seq, n.uid)
            except Exception as e:  # noqa: BLE001
                log.debug("Résultats illisibles pour %s : %s", n.number, e)
                stats["errors"] += 1
                continue
            lots = r.lots()
            if not lots:
                continue
            stats["with_results"] += 1

            for lot, bidders in sorted(lots.items()):
                stats["priced_lots"] += 1
                key = (n.uid, lot)
                already = key in known
                if already:
                    stats["already"] += 1
                out.append(Candidate(
                    uid=n.uid, lot=lot,
                    ref=n.number + (f" — lot {lot}" if lot and len(lots) > 1 else ""),
                    buyer=n.buyer or "",
                    tdate=(n.published_at or "")[:10],
                    source=n.source,
                    title=(n.title_fr or n.title_ar or "")[:160],
                    url=n.url or "",
                    already=already,
                    bids=[{"name": b.company, "reg_no": b.reg_no,
                           "amount": b.price, "rank": b.rank,
                           "retained": b.retained} for b in bidders],
                ))

    stats["candidates"] = len([c for c in out if not c.already])
    return {"stats": stats, "candidates": [c.as_dict() for c in out]}


def commit(store, candidates: list[dict]) -> dict:
    """Verse les lots retenus. Les entrées déjà présentes sont ignorées."""
    known = store.market_keys()
    created = skipped = 0
    conflicts: list[dict] = []
    for c in candidates:
        key = (c.get("uid") or "", c.get("lot") or "")
        if key in known:
            skipped += 1
            continue
        bids = [b for b in (c.get("bids") or []) if float(b.get("amount") or 0) > 0]
        if not bids:
            skipped += 1
            continue
        r = store.save_market_tender(
            ref=c.get("ref") or "", buyer=c.get("buyer") or "",
            tdate=c.get("tdate") or "", items={}, bids=bids,
            note=(c.get("title") or "")[:200],
            uid=c.get("uid") or None, lot=c.get("lot") or "")
        created += 1
        known.add(key)
        conflicts.extend(r.get("conflicts") or [])
    log.info("Import prix marché : %d créé(s), %d ignoré(s)", created, skipped)
    return {"created": created, "skipped": skipped, "conflicts": conflicts}
