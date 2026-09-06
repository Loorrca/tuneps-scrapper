"""
Résultats des avis : qui a été retenu, à quel prix.

TUNEPS ne publie les résultats qu'une fois le délai de dépôt passé. Les points
d'entrée sont publics (aucune authentification), mais diffèrent entre les
consultations et les appels d'offres.

CONSULTATIONS ------------------------------------------------------------
  GET /api2/portail/shopResultats/checkResultat?shopNo=&shopModSeq=
      -> payload "Y" | "N"   : les résultats d'ouverture sont publiés
  GET /api2/portail/shopResultats/cehckWinner?shopNo=&shopModSeq=
      -> payload "Y" | "N"   : un attributaire est désigné
      (la faute de frappe « cehck » est celle du serveur, ne pas la corriger)
  GET /api2/portail/shopResultats/openProgressSupCls/data?shopNo=&shopModSeq=
      -> la liste des soumissionnaires : société, prix, rang, retenu ou non
  GET /api2/portail/shopResultats/spFailResult/data?shopNo=&shopModSeq=
      -> lots déclarés infructueux

APPELS D'OFFRES ----------------------------------------------------------
  GET /api2/portail/bid/master/execTypeChkOpen?bidNo=&bidModSeq=   -> "Y"|"N"
  GET /api2/portail/bid/master/execTypeChkEval?bidNo=&bidModSeq=   -> "Y"|"N"
  GET /api2/portail/bid/check/publication?bidNo=&bidModSeq=        -> "Y"|"N"
      (respectivement : ouverture publiée, évaluation publiée, marché attribué)

  Les tableaux détaillés des A.O. (`ranking/rankTenderer/data`,
  `bid/listWinners`) n'ont pas livré leur signature exacte lors de l'analyse du
  portail : ils sont tentés au mieux et leur échec est sans conséquence. On
  affiche alors les indicateurs et le lien vers TUNEPS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any

from .client import BASE, TunepsClient

log = logging.getLogger(__name__)

P = f"{BASE}/api2/portail"


@dataclass
class Bidder:
    """Un soumissionnaire tel que le portail le publie."""
    company: str = ""
    reg_no: str = ""
    price: float | None = None
    rank: int | None = None
    retained: bool | None = None      # None = statut non communiqué
    status_label: str = ""
    reason: str = ""
    submitted_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Result:
    uid: str = ""
    published: bool = False           # des résultats sont consultables
    winner_declared: bool = False     # un attributaire est désigné
    opening_published: bool = False   # A.O. : résultats d'ouverture
    eval_published: bool = False      # A.O. : résultats d'évaluation
    bidders: list[Bidder] = field(default_factory=list)
    failed_lots: list[dict] = field(default_factory=list)
    detail_available: bool = False    # on a pu récupérer le détail, pas juste les drapeaux
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bidders"] = [b.as_dict() for b in self.bidders]
        return d

    @property
    def winner(self) -> Bidder | None:
        """Le mieux classé parmi les retenus."""
        keep = [b for b in self.bidders if b.retained and b.rank is not None]
        if not keep:
            keep = [b for b in self.bidders if b.retained]
        if not keep:
            return None
        return sorted(keep, key=lambda b: (b.rank if b.rank is not None else 9999))[0]


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _bidder_from_shop(r: dict) -> Bidder:
    label = (r.get("cdNmStrFr") or "").strip()
    reason = (r.get("ineligibleReason") or "").strip()
    # ineligibleCd "00" = recevable ; toute autre valeur = écarté
    code = (r.get("ineligibleCd") or "").strip()
    retained: bool | None
    if label:
        retained = label.lower().startswith("retenu")
    elif code:
        retained = code == "00"
    else:
        retained = None
    return Bidder(
        company=(r.get("bizRegNm") or "").strip(),
        reg_no=(r.get("bizRegNo") or "").strip(),
        # totalShopPriceCorr = prix après correction, c'est celui qui fait foi
        price=_f(r.get("totalShopPriceCorr")) or _f(r.get("totalShopPriceDcExch")),
        rank=_i(r.get("rankDep")),
        retained=retained,
        status_label=label,
        reason="" if reason in ("", "-") else reason,
        submitted_at=(r.get("spRecvDtReal") or "")[:19],
    )


class ResultsClient:
    """Interroge les points d'entrée « résultats ». Ne lève jamais : renvoie un Result."""

    def __init__(self, client: TunepsClient | None = None, **kw):
        self.c = client or TunepsClient(**kw)

    # ------------------------------------------------------------------
    def _get(self, url: str) -> Any:
        """GET JSON, en réutilisant la session (et le magasin TLS) du client."""
        try:
            r = self.c.session.get(url, timeout=self.c.timeout)
            r.raise_for_status()
            return (r.json() or {}).get("payload")
        except Exception as e:  # noqa: BLE001
            log.debug("GET %s a échoué : %s", url, e)
            raise

    def _flag(self, url: str) -> bool:
        try:
            return str(self._get(url)).strip().upper() == "Y"
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    def for_notice(self, source: str, number: str, mod_seq: str = "00",
                   uid: str = "") -> Result:
        res = Result(uid=uid or f"{source}:{number}:{mod_seq}")
        try:
            if source == "consultation":
                self._consultation(res, number, mod_seq)
            else:
                self._appel_offres(res, number, mod_seq)
        except Exception as e:  # noqa: BLE001
            log.warning("Résultats indisponibles pour %s : %s", number, e)
            res.error = str(e)[:200]
        return res

    # ------------------------------------------------------------------
    def _consultation(self, res: Result, no: str, seq: str) -> None:
        q = f"shopNo={no}&shopModSeq={seq}"
        res.published = self._flag(f"{P}/shopResultats/checkResultat?{q}")
        res.winner_declared = self._flag(f"{P}/shopResultats/cehckWinner?{q}")
        res.opening_published = res.published
        if not res.published:
            return
        try:
            rows = self._get(f"{P}/shopResultats/openProgressSupCls/data?{q}") or []
            # Le portail renvoie une ligne par (soumissionnaire, lot) : on garde
            # la meilleure ligne de chaque société pour ne pas la lister 3 fois.
            best: dict[str, Bidder] = {}
            for row in rows:
                b = _bidder_from_shop(row)
                cle = b.reg_no or b.company
                cur = best.get(cle)
                if cur is None or (b.rank or 9999) < (cur.rank or 9999):
                    best[cle] = b
            res.bidders = sorted(
                best.values(),
                key=lambda b: (b.rank if b.rank is not None else 9999, b.price or 0))
            res.detail_available = bool(res.bidders)
        except Exception as e:  # noqa: BLE001
            log.debug("Liste des soumissionnaires indisponible pour %s : %s", no, e)
        try:
            res.failed_lots = self._get(f"{P}/shopResultats/spFailResult/data?{q}") or []
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    def _appel_offres(self, res: Result, no: str, seq: str) -> None:
        q = f"bidNo={no}&bidModSeq={seq}"
        res.opening_published = self._flag(f"{P}/bid/master/execTypeChkOpen?{q}")
        res.eval_published = self._flag(f"{P}/bid/master/execTypeChkEval?{q}")
        res.winner_declared = self._flag(f"{P}/bid/check/publication?{q}")
        res.published = res.opening_published or res.eval_published or res.winner_declared
        if not res.published:
            return
        # Tentative de détail : signature non confirmée côté portail, on essaie
        # les deux formes rencontrées dans le code de l'application et on
        # abandonne silencieusement en cas d'échec.
        body = {"dataSearch": [{"key": "bidNo", "value": no, "specificSearch": "like"}],
                "listSort": [], "listCol": []}
        for url in (f"{P}/ranking/rankTenderer/data", f"{P}/ranking/rankLot/data"):
            try:
                r = self.c.session.post(url, json=body, timeout=self.c.timeout)
                if r.status_code != 200:
                    continue
                rows = ((r.json() or {}).get("payload") or {})
                rows = rows.get("data") if isinstance(rows, dict) else rows
                if not rows:
                    continue
                res.bidders = sorted(
                    (_bidder_from_shop(x) for x in rows),
                    key=lambda b: (b.rank if b.rank is not None else 9999))
                res.detail_available = bool(res.bidders)
                break
            except Exception:  # noqa: BLE001
                continue
