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
rapprochement avec vos articles qui demande votre jugement. Les entrées
importées arrivent donc sans composition, et n'entrent dans aucun calcul tant
que vous ne l'avez pas renseignée.

Une entrée par lot
------------------
Un marché à trois lots, c'est trois compositions différentes et trois montants
par concurrent. On crée donc trois entrées. Les fusionner en additionnant les
montants serait faux dès qu'un concurrent n'a pas soumissionné sur tous les
lots : son total ne couvrirait pas la composition qu'on lui attribue, et
l'écart irait polluer l'estimation de tous ses autres marchés.

OÙ PASSE LE TEMPS — et ce qui a été fait
-----------------------------------------
La première version mettait plusieurs minutes. Quatre causes, traitées :

1. **Cinq requêtes par avis.** `for_notice` interroge trois ou quatre drapeaux
   « oui/non » avant d'aller chercher le tableau. Ces drapeaux servent à
   l'affichage, pas ici : l'absence de tableau dit déjà tout. On utilise
   `priced_lots()`, une seule requête.
2. **Tout en série.** Chaque requête attend la précédente, avec la latence
   d'un lien tunisien depuis un Raspberry Pi. Les avis sont maintenant
   interrogés en parallèle, chaque fil avec sa propre session HTTP —
   `requests.Session` n'est pas réputée sûre entre fils.
3. **Les avis déjà importés étaient réinterrogés** avant d'être écartés. Ils
   sont maintenant filtrés avant tout appel réseau.
4. **Le balayage mensuel télécharge TOUS les avis du mois** — plusieurs
   milliers de consultations, quelques mégaoctets à analyser sur un Pi. Quand
   la veille a déjà vu la période, on part de sa base : zéro requête pour
   cette phase. Le balayage complet reste disponible pour rattraper les mois
   antérieurs à l'installation de la veille.

Les durées de chaque phase sont mesurées et renvoyées dans `stats.timing` :
en cas de lenteur, on regarde au lieu de supposer.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)

# Assez pour couvrir la latence, pas assez pour ressembler à une attaque : le
# portail est un service public, et le Pi n'a rien d'une machine de course.
WORKERS = 6


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
    already: bool = False

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


def _in_period(published_at: str, since: str) -> bool:
    return (published_at or "")[:7] >= since[:7]


def scan(client, matcher, results_client, known: set[tuple[str, str]],
         since: str = "2026-05", limit: int | None = None,
         progress: Callable[[str], None] | None = None,
         notices: list | None = None,
         make_client: Callable[[], object] | None = None,
         workers: int = WORKERS) -> dict:
    """Trouve les lots chiffrés qui concernent l'activité. N'écrit rien.

    `notices` : liste d'avis déjà connus (base de la veille). Fournie, elle
    remplace le balayage du portail — de loin la phase la plus coûteuse.
    `make_client` : fabrique une session HTTP par fil d'exécution.
    """
    t0 = time.monotonic()
    stats = {"months": 0, "scanned": 0, "matched": 0, "checked": 0,
             "with_results": 0, "priced_lots": 0, "already": 0, "skipped_known": 0,
             "errors": 0, "since": since, "source": "base" if notices is not None else "portail",
             "timing": {}}

    # --- 1. de quels avis part-on ?
    if notices is not None:
        retenus = [n for n in notices if _in_period(getattr(n, "published_at", ""), since)]
        stats["scanned"] = len(retenus)
        stats["matched"] = len(retenus)
    else:
        retenus = []
        for (y, m) in _months(since):
            stats["months"] += 1
            if progress:
                progress(f"{y:04d}-{m:02d}")
            try:
                mois = client.month_window(y, m)
            except Exception as e:  # noqa: BLE001
                log.warning("Fenêtre %04d-%02d illisible : %s", y, m, e)
                stats["errors"] += 1
                continue
            stats["scanned"] += len(mois)
            for n in mois:
                if matcher.match(*n.titles).matched:
                    stats["matched"] += 1
                    retenus.append(n)
    stats["timing"]["decouverte_s"] = round(time.monotonic() - t0, 1)

    # --- 2. écarter avant d'interroger : un avis déjà importé ne coûte rien
    deja = {u for u, _ in known}
    a_voir = [n for n in retenus if n.uid not in deja]
    stats["skipped_known"] = len(retenus) - len(a_voir)
    if limit is not None:
        a_voir = a_voir[:int(limit)]
    stats["checked"] = len(a_voir)

    # --- 3. les résultats, en parallèle
    t1 = time.monotonic()
    out: list[Candidate] = []
    if a_voir:
        n_fils = max(1, min(int(workers), len(a_voir)))
        # une session HTTP par fil : requests.Session n'est pas sûre entre fils
        pool = [results_client] + [
            (make_client() if make_client else results_client) for _ in range(n_fils - 1)]

        def travail(i_n):
            i, n = i_n
            rc = pool[i % len(pool)]
            try:
                return n, rc.priced_lots(n.source, n.number, n.mod_seq), None
            except Exception as e:  # noqa: BLE001
                return n, {}, e

        with ThreadPoolExecutor(max_workers=n_fils) as ex:
            for n, lots, err in ex.map(travail, enumerate(a_voir)):
                if err is not None:
                    log.debug("Résultats illisibles pour %s : %s", n.number, err)
                    stats["errors"] += 1
                    continue
                if not lots:
                    continue
                stats["with_results"] += 1
                for lot, bidders in sorted(lots.items()):
                    stats["priced_lots"] += 1
                    out.append(Candidate(
                        uid=n.uid, lot=lot,
                        ref=n.number + (f" — lot {lot}" if lot and len(lots) > 1 else ""),
                        buyer=n.buyer or "",
                        tdate=(n.published_at or "")[:10],
                        source=n.source,
                        title=(n.title_fr or n.title_ar or "")[:160],
                        url=n.url or "",
                        already=(n.uid, lot) in known,
                        bids=[{"name": b.company, "reg_no": b.reg_no,
                               "amount": b.price, "rank": b.rank,
                               "retained": b.retained} for b in bidders],
                    ))

    stats["timing"]["resultats_s"] = round(time.monotonic() - t1, 1)
    stats["timing"]["total_s"] = round(time.monotonic() - t0, 1)
    stats["candidates"] = len([c for c in out if not c.already])
    log.info("Import : %d avis retenus, %d interrogés, %d lots chiffrés en %ss "
             "(découverte %ss, résultats %ss)", stats["matched"], stats["checked"],
             stats["priced_lots"], stats["timing"]["total_s"],
             stats["timing"]["decouverte_s"], stats["timing"]["resultats_s"])
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
