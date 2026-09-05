"""
Client de l'API publique du portail TUNEPS (www.tuneps.tn).

Deux ressources publiques, sans authentification :

  POST /api2/portail/bid/master/data        -> avis d'appel d'offres
  POST /api2/portail/vSpShopMaster/data     -> consultations (« Shopping Mall »)

Corps de requête (SearchObject de l'application Angular) :

    {
      "dataSearch": [ {"key": <colonne>, "value": <valeur>, "specificSearch": <op>} ],
      "listSort":   [ {"nameCol": <colonne>, "direction": "asc"|"desc"} ],
      "listCol":    []
    }

Opérateurs observés :
  "like"  -> LIKE insensible à la casse ; les jokers SQL % et _ fonctionnent.
  "LIKE"  -> égalité stricte, sensible à la casse (piège : ce n'est PAS un LIKE).

Plusieurs entrées de dataSearch sont combinées en ET.

Réponse : {"code":"200","payload":{"total":N,"data":[ ... ]}}

Astuce de fenêtrage temporel : il n'existe pas d'opérateur de comparaison de
dates, mais le numéro d'avis encode l'année et le mois
(bidNo = "20260900572", shopNo = "S20260900001"). Un `like` sur ce préfixe
donne donc tous les avis d'un mois donné, en quelques centaines de lignes.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import requests

log = logging.getLogger(__name__)

BASE = "https://www.tuneps.tn"
BID_URL = f"{BASE}/api2/portail/bid/master/data"
SHOP_URL = f"{BASE}/api2/portail/vSpShopMaster/data"

BID_DETAIL = f"{BASE}/portail/offres/details/{{mid}}/{{no}}"
SHOP_DETAIL = f"{BASE}/portail/consultations/consultationdetails/{{mid}}/{{no}}"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


@dataclass
class Notice:
    """Un avis, normalisé, quelle que soit sa source."""
    source: str          # "ao" (appel d'offres) | "consultation"
    number: str          # bidNo / shopNo
    mod_seq: str
    master_id: int | None
    title_fr: str
    title_ar: str
    title_en: str
    buyer: str
    published_at: str
    deadline_at: str
    url: str

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.number}:{self.mod_seq}"

    @property
    def titles(self) -> tuple[str, str, str]:
        return (self.title_fr, self.title_ar, self.title_en)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _from_bid(r: dict) -> Notice:
    mid, no = r.get("epBidMasterId"), _s(r.get("bidNo"))
    return Notice(
        source="ao",
        number=no,
        mod_seq=_s(r.get("bidModSeq")) or "00",
        master_id=mid,
        title_fr=_s(r.get("bidNmFr")),
        title_ar=_s(r.get("bidNmAr")),
        title_en=_s(r.get("bidNmEn")),
        buyer=_s(r.get("bidInstNm")),
        published_at=_s(r.get("publicDt"))[:19],
        deadline_at=_s(r.get("bdRecvEndDt"))[:19],
        url=BID_DETAIL.format(mid=mid, no=no) if mid else f"{BASE}/portail/offres",
    )


def _from_shop(r: dict) -> Notice:
    mid, no = r.get("spShopMasterId"), _s(r.get("shopNo"))
    return Notice(
        source="consultation",
        number=no,
        mod_seq=_s(r.get("shopModSeq")) or "00",
        master_id=mid,
        title_fr=_s(r.get("shopNmFr")),
        title_ar=_s(r.get("shopNmAr")),
        title_en=_s(r.get("shopNmEn")),
        buyer=_s(r.get("instNm")),
        published_at=_s(r.get("publicDt"))[:19],
        deadline_at=_s(r.get("spRecvEndDt"))[:19],
        url=SHOP_DETAIL.format(mid=mid, no=no) if mid else f"{BASE}/portail/consultations",
    )


class TunepsClient:
    def __init__(self, timeout: int = 60, retries: int = 3, pause: float = 0.4,
                 verify_ssl: bool = True, ca_cache: "Path | str | None" = None):
        self.timeout = timeout
        self.retries = retries
        self.pause = pause
        self.verify_ssl = verify_ssl
        self.ca_cache = Path(ca_cache) if ca_cache else Path("data/tuneps-ca.pem")
        self._tls_repaired = False
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.session.headers.update({
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": BASE,
            "Referer": f"{BASE}/portail",
        })
        if verify_ssl and self.ca_cache.exists():
            self.session.verify = str(self.ca_cache)
            self._tls_repaired = True

    # ------------------------------------------------------------------
    def _repair_tls(self) -> bool:
        """
        Complète une chaîne de certificats tronquée (le cas de www.tuneps.tn),
        comme le fait un navigateur. Une seule tentative par exécution.
        """
        if self._tls_repaired or not self.verify_ssl:
            return False
        self._tls_repaired = True
        from . import tlsfix
        try:
            bundle = tlsfix.build_ca_bundle("www.tuneps.tn", self.ca_cache)
        except Exception as e:  # noqa: BLE001
            log.error("Chaîne TLS irréparable automatiquement : %s", e)
            return False
        self.session.verify = str(bundle)
        log.info("Chaîne TLS complétée automatiquement (magasin : %s)", bundle)
        return True

    # ------------------------------------------------------------------
    def _post(self, url: str, data_search: list[dict]) -> list[dict]:
        body = {"dataSearch": data_search, "listSort": [], "listCol": []}
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                r = self.session.post(url, json=body, timeout=self.timeout)
                r.raise_for_status()
                payload = (r.json() or {}).get("payload") or {}
                rows = payload.get("data") or []
                log.debug("POST %s %s -> %d lignes", url, data_search, len(rows))
                time.sleep(self.pause)
                return rows
            except requests.exceptions.SSLError as e:
                last = e
                # Une erreur de certificat est déterministe : réessayer à
                # l'identique ne sert à rien. On répare la chaîne, ou on s'arrête.
                if self._repair_tls():
                    continue
                log.error("Échec de vérification TLS : %s", e)
                log.error("Diagnostic détaillé :  ./veille doctor")
                break
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt >= self.retries:
                    break
                wait = min(2 ** attempt, 20)
                log.warning("Échec requête (%s/%s) : %s — nouvelle tentative dans %ss",
                            attempt, self.retries, e, wait)
                time.sleep(wait)
        log.error("Requête abandonnée : %s", last)
        return []

    # ------------------------------------------------------------------
    def month_window(self, year: int, month: int) -> list[Notice]:
        """Tous les avis dont le numéro commence par AAAAMM (les deux sources)."""
        pfx = f"{year:04d}{month:02d}"
        out: list[Notice] = []

        for row in self._post(BID_URL, [{"key": "bidNo", "value": f"{pfx}%", "specificSearch": "like"}]):
            no = _s(row.get("bidNo"))
            if no.startswith(pfx):                      # le serveur encadre la valeur de %
                out.append(_from_bid(row))

        for row in self._post(SHOP_URL, [{"key": "shopNo", "value": f"S{pfx}%", "specificSearch": "like"}]):
            no = _s(row.get("shopNo"))
            if no.startswith(f"S{pfx}"):
                out.append(_from_shop(row))

        return out

    def recent(self, months_back: int = 1, today: dt.date | None = None) -> list[Notice]:
        """Fenêtre glissante : mois courant + `months_back` mois précédents."""
        today = today or dt.date.today()
        seen: set[str] = set()
        out: list[Notice] = []
        y, m = today.year, today.month
        for _ in range(months_back + 1):
            for n in self.month_window(y, m):
                if n.uid not in seen:
                    seen.add(n.uid)
                    out.append(n)
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        return out

    # ------------------------------------------------------------------
    def keyword_search(self, stems: Iterable[str]) -> list[Notice]:
        """
        Recherche `like %stem%` côté serveur sur toutes les colonnes de libellé,
        sur l'historique complet. Sert au rattrapage initial (backfill) et de
        filet de sécurité — la fenêtre mensuelle reste le mode nominal.
        """
        seen: set[str] = set()
        out: list[Notice] = []
        plan = [
            (BID_URL, ["bidNmFr", "bidNmAr", "bidNmEn"], _from_bid, "bidNo"),
            (SHOP_URL, ["shopNmFr", "shopNmAr", "shopNmEn"], _from_shop, "shopNo"),
        ]
        for stem in stems:
            for url, cols, conv, _key in plan:
                for col in cols:
                    crit = [{"key": col, "value": f"%{stem}%", "specificSearch": "like"}]
                    for row in self._post(url, crit):
                        n = conv(row)
                        if n.uid not in seen:
                            seen.add(n.uid)
                            out.append(n)
        return out
