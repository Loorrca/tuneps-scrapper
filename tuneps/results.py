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

  POST /api2/portail/ranking/rankLot/data?bidNo=&bidModSeq=
      -> une ligne par (société, LOT) : matricule fiscal, raison sociale,
         montant, rang, retenu ou non, motif.

      ATTENTION à la forme de la requête, c'est ce qui avait fait échouer une
      première tentative : les paramètres passent par la CHAÎNE DE REQUÊTE, pas
      par un corps `dataSearch`. Il faut malgré tout un corps JSON, même vide —
      sans corps le serveur répond 415, et en GET il répond 405.

  POST /api2/portail/bid/listLotInfructueux?bidNo=&bidModSeq=   -> lots infructueux
  GET  /api2/portail/vBidCls/lot?bidNo=                         -> liste des lots

CE QUE CHAQUE SOURCE PUBLIE RÉELLEMENT (mesuré sur le portail, sept. 2026)
--------------------------------------------------------------------------
  A.O.           : ~2 sur 3 exposent le tableau chiffré complet. C'est la
                   source riche.
  Consultations  : la liste des soumissionnaires est presque toujours là, mais
                   SANS montant. La version chiffrée existe (mêmes champs plus
                   totalShopPriceCorr et rankDep) et n'est publiée que très
                   rarement — de l'ordre d'une consultation sur quarante.

  D'où l'ordre des priorités : on lit ce qui est chiffré, quelle que soit la
  source, et on ignore silencieusement le reste.

NUMÉROTATION DES LOTS
---------------------
  `bidCls` côté A.O., `shopCls` côté consultation. Une société qui soumissionne
  sur trois lots produit trois lignes, avec trois montants distincts. Ces
  lignes NE DOIVENT PAS être fusionnées pour l'usage « prix marché » : chaque
  lot est une observation à part entière, avec sa propre composition.
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
    lot: str = ""                     # bidCls (A.O.) ou shopCls (consultation)

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

    def lots(self) -> dict[str, list[Bidder]]:
        """Les soumissionnaires groupés par lot, ne gardant que les lignes chiffrées.

        C'est la forme qu'attend le module « prix marché » : chaque lot est une
        observation distincte, avec sa propre composition d'articles.
        """
        out: dict[str, list[Bidder]] = {}
        for b in self.bidders:
            if not b.price or b.price <= 0:
                continue
            out.setdefault(b.lot or "", []).append(b)
        return out

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


def _retained(label: str, code: str) -> bool | None:
    """Retenu, écarté, ou non communiqué. Ne jamais transformer un silence en oui."""
    if label:
        low = label.lower()
        if low.startswith("non retenu") or low.startswith("nonretenu"):
            return False
        if low.startswith("retenu"):
            return True
    if code:
        return code.strip() == "00"
    return None


def _clean(v) -> str:
    t = str(v or "").strip()
    return "" if t in ("", "-") else t


def _bidder_from_shop(r: dict) -> Bidder:
    """Une ligne de consultation (openProgressSupCls).

    Deux formes coexistent selon l'avancement de la procédure : une forme
    réduite sans prix ni rang, et la forme complète. On lit ce qui est là.
    """
    label = _clean(r.get("cdNmStrFr"))
    return Bidder(
        company=_clean(r.get("bizRegNm")),
        reg_no=_clean(r.get("bizRegNo")),
        # totalShopPriceCorr = prix après correction, c'est celui qui fait foi
        price=_f(r.get("totalShopPriceCorr")) or _f(r.get("totalShopPriceDcExch")),
        rank=_i(r.get("rankDep")) or _i(r.get("rk")),
        retained=_retained(label, _clean(r.get("ineligibleCd"))),
        status_label=label,
        reason=_clean(r.get("ineligibleReason")),
        submitted_at=_clean(r.get("spRecvDtReal"))[:19],
        lot=_clean(r.get("shopCls")),
    )


def _bidder_from_bid(r: dict) -> Bidder:
    """Une ligne d'appel d'offres (ranking/rankLot) — une par société ET par lot."""
    # cdPfFinaFr porte le verdict financier, cdNmStrFr la recevabilité ;
    # le premier est le plus proche de « a-t-il emporté le lot ».
    label = _clean(r.get("cdPfFinaFr")) or _clean(r.get("cdNmStrFr"))
    return Bidder(
        company=_clean(r.get("bizRegNm")),
        reg_no=_clean(r.get("bizRegNo")),
        price=_f(r.get("totalBidPriceDcExch")),
        rank=_i(r.get("rank")) or _i(r.get("rk")),
        retained=_retained(label, _clean(r.get("ineligibleCd"))),
        status_label=label,
        reason=_clean(r.get("finaResultReason")) or _clean(r.get("techResultReason")),
        submitted_at=_clean(r.get("bdRecvDt"))[:19],
        lot=_clean(r.get("bidCls")),
    )


def _sorted_bidders(it) -> list[Bidder]:
    """Par lot, puis par rang. Les lignes vides (ni société ni prix) sautent."""
    keep = [b for b in it if b.company or b.reg_no]
    return sorted(keep, key=lambda b: (b.lot or "", b.rank if b.rank is not None else 9999,
                                       b.price or 0))


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

    def _post(self, url: str) -> Any:
        """POST « paramètres dans l'URL, corps JSON vide ».

        C'est la forme qu'attendent les points d'entrée de classement : sans
        corps le serveur répond 415, et en GET il répond 405.
        """
        try:
            r = self.c.session.post(url, json={}, timeout=self.c.timeout)
            r.raise_for_status()
            payload = (r.json() or {}).get("payload")
            if isinstance(payload, dict):
                return payload.get("data")
            return payload
        except Exception as e:  # noqa: BLE001
            log.debug("POST %s a échoué : %s", url, e)
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
            # Une ligne par (société, lot). On ne fusionne PAS : chaque lot a sa
            # propre composition, donc son propre montant. Fusionner ferait
            # disparaître des observations exploitables.
            res.bidders = _sorted_bidders(_bidder_from_shop(r) for r in rows)
            res.detail_available = any(b.price for b in res.bidders)
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
        try:
            rows = self._post(f"{P}/ranking/rankLot/data?{q}") or []
            res.bidders = _sorted_bidders(_bidder_from_bid(r) for r in rows)
            res.detail_available = any(b.price for b in res.bidders)
        except Exception as e:  # noqa: BLE001
            log.debug("Classement indisponible pour %s : %s", no, e)
        try:
            res.failed_lots = self._post(f"{P}/bid/listLotInfructueux?{q}") or []
        except Exception:  # noqa: BLE001
            pass
