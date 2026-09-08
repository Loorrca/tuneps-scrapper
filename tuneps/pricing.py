"""
Prix des concurrents : ce qu'on peut en déduire, et rien de plus.

Le problème
-----------
Un concurrent dépose UN montant pour un marché qui contient plusieurs articles.
Si on note p_j son prix unitaire pour l'article j et q_ij la quantité de cet
article dans le marché i, alors chaque montant observé T_i donne une équation :

    q_i1·p_1 + q_i2·p_2 + … + q_in·p_n = T_i

Avec assez de marchés on pourrait résoudre le système. Deux obstacles :

1. Il est presque toujours sous-déterminé au début — moins de marchés que
   d'articles, ou des articles vus une seule fois.
2. Les prix ne sont pas constants : le même concurrent ne facture pas
   identiquement tous les acheteurs, et ses prix bougent dans le temps.

On ne cherche donc pas LA solution, qui n'existe pas, mais **l'ensemble des
prix compatibles avec tout ce qu'on a observé**, et on en publie les bornes.

La méthode
----------
Deux passes de programmation linéaire.

Passe 1 — cohérence. On cherche le plus petit écart relatif t tel que les
prix puissent reproduire tous les montants observés à t près :

    | Σ_j q_ij·p_j − T_i |  ≤  t · T_i        pour tout marché i
    p_j ≥ 0
    minimiser t

Le t obtenu est une mesure de la dispersion réelle des prix du concurrent :
« ses montants ne s'expliquent pas par un tarif unique à mieux que ±9 % ».
Ce n'est pas un réglage arbitraire, c'est un résultat.

Passe 2 — intervalles. À dispersion figée, on minimise puis on maximise chaque
p_j séparément. Deux résolutions par article. Les bornes obtenues sont
**garanties sous l'hypothèse de dispersion retenue** : aucune valeur en dehors
ne peut expliquer les montants observés si les prix du concurrent varient
d'au plus cette dispersion. Ce ne sont pas des intervalles de confiance
statistiques.

Le piège du t* (mesuré, et corrigé)
-----------------------------------
Il est tentant d'utiliser directement t* comme dispersion de la passe 2.
C'est faux, et dangereusement : t* SOUS-ESTIME la dispersion réelle, parce
que les 16 prix libres absorbent une partie du bruit. Avec 16 articles et n
marchés, l'ajustement rétrécit les écarts d'un facteur ≈ √(1 − 16/n).

Mesuré sur données simulées dont on connaît les vrais prix, avec les
intervalles calculés à t* exactement :

    marchés   dispersion réelle   vrais prix dans l'intervalle
      40            ±10 %                    4 %
      60            ±10 %                   15 %

Autrement dit des intervalles étroits, nets, rassurants — et faux 90 fois sur
100. On corrige donc t* de ce rétrécissement avant de s'en servir :

    dispersion = t* / √(1 − k/n) × 1,1        (k = articles identifiés)

Le facteur 1,1 est une marge de sécurité calibrée sur les mêmes simulations.
Avec cette correction la couverture remonte à 98–100 % de 25 à 100 marchés et
de ±5 % à ±20 % de dispersion réelle, et la dispersion retenue retombe à
environ 1,0 fois la dispersion réelle.

`dispersion` reste réglable : c'est le seul paramètre de jugement du calcul,
et le moyen de le calibrer est de cocher « c'est nous » sur votre société,
dont vous connaissez les vrais prix unitaires.

Ce que la méthode ne peut pas faire
-----------------------------------
Deux articles qui apparaissent toujours dans le même rapport de quantités ne
sont jamais séparables : seule leur combinaison est identifiée, et leurs
intervalles individuels restent larges quel que soit le nombre de marchés.
C'est une propriété des données, pas un défaut du calcul — d'où le comptage
des observations qui contraignent réellement chaque article, publié à côté de
chaque intervalle.

Prédire le montant d'un concurrent sur un marché
------------------------------------------------
Surtout PAS en sommant les intervalles article par article : cela ignore le
fait que les prix sont liés entre eux et donne une fourchette bien plus large
que la réalité. On optimise directement la somme sur le même domaine
(`predict()`), ce qui donne la fourchette juste.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

# Marge de sécurité sur la dispersion corrigée. Calibrée par simulation :
# à 1,0 la couverture tombe à 96 % sur les gros jeux de données, à 1,1 elle
# tient 98-100 % partout, au-delà les intervalles s'élargissent pour rien.
SAFETY = 1.1

# Demi-vie par défaut de l'influence d'une observation, en mois. Calibrée par
# simulation : à 3 mois on sur-escompte le passé, à 12 on le sous-escompte.
HALFLIFE_MONTHS = 6.0

# Une observation très ancienne doit peser peu, pas disparaître : sans plafond,
# son poids explose et elle cesse de contraindre quoi que ce soit.
MAX_WEIGHT = 8.0

# Marge numérique : figer t exactement à sa valeur optimale rend parfois la
# passe 2 infaisable à cause des arrondis flottants.
_EPS_REL = 1e-6
_EPS_ABS = 1e-9


class SolverUnavailable(RuntimeError):
    """scipy n'est pas installé sur cette machine."""


def _linprog():
    try:
        from scipy.optimize import linprog
    except Exception as e:  # noqa: BLE001
        raise SolverUnavailable(
            "Le calcul des prix a besoin de scipy. Sur Debian/Raspberry Pi OS : "
            "sudo apt install python3-scipy (et un venv créé avec "
            "--system-site-packages)."
        ) from e
    return linprog


@dataclass
class Observation:
    """Un montant déposé par un concurrent sur un marché de composition connue."""
    tender_id: int
    label: str                      # référence ou acheteur, pour l'affichage
    qty: dict[int, float]           # article_id -> quantité
    amount: float
    tdate: str = ""                 # AAAA-MM-JJ, pour la pondération temporelle

    def total(self, prices: dict[int, float]) -> float:
        return sum(q * prices.get(a, 0.0) for a, q in self.qty.items())

    def age_months(self, today: dt.date | None = None) -> float:
        """Âge en mois. Une date absente ou illisible vaut « récent » : mieux
        vaut ne pas escompter que d'escompter au hasard."""
        if not self.tdate:
            return 0.0
        try:
            d = dt.date.fromisoformat(self.tdate[:10])
        except ValueError:
            return 0.0
        jours = ((today or dt.date.today()) - d).days
        return max(0.0, jours / 30.44)


def weights(obs: Sequence[Observation], halflife: float | None,
            today: dt.date | None = None) -> list[float]:
    """Multiplicateur de tolérance par observation : 2^(âge / demi-vie)."""
    if not halflife or halflife <= 0:
        return [1.0] * len(obs)
    out = []
    for o in obs:
        w = 2.0 ** (o.age_months(today) / float(halflife))
        out.append(min(MAX_WEIGHT, w))
    return out


def effective_df(obs: Sequence[Observation], articles: Sequence[int]
                 ) -> tuple[int, int]:
    """(n, k) utiles pour le calcul du rétrécissement.

    Un article vu dans un seul marché absorbe entièrement le résidu de ce
    marché : ni l'équation ni l'inconnue n'apprennent quoi que ce soit sur la
    dispersion. Les compter gonfle la correction et élargit les fourchettes de
    tous les autres articles — ce que les articles rares ne doivent pas faire.
    """
    cnt = {a: sum(1 for o in obs if o.qty.get(a, 0)) for a in articles}
    vus = [a for a in articles if cnt[a]]
    uniques = {a for a in vus if cnt[a] == 1}
    absorbees = sum(1 for o in obs if any(a in uniques for a in o.qty if o.qty[a]))
    n_eff = max(1, len(obs) - absorbees)
    k_eff = max(0, len(vus) - len(uniques))
    return n_eff, k_eff


@dataclass
class ArticleRange:
    article_id: int
    low: float | None = None        # None = article jamais observé
    high: float | None = None
    n_obs: int = 0                  # marchés où cet article est présent

    @property
    def width(self) -> float | None:
        if self.low is None or self.high is None:
            return None
        return self.high - self.low

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["width"] = self.width
        return d


@dataclass
class Estimate:
    competitor_id: int = 0
    n_obs: int = 0
    feasible: bool = False
    tolerance: float = 0.0          # t* mesuré : plancher de dispersion
    dispersion: float = 0.0         # dispersion retenue pour les intervalles
    dispersion_source: str = ""     # "corrigée" | "imposée"
    halflife: float = 0.0           # demi-vie appliquée, 0 = aucune
    n_eff: int = 0                  # équations utiles au calcul de la correction
    k_eff: int = 0                  # inconnues utiles (articles rares exclus)
    rare: list[int] = field(default_factory=list)   # articles vus une seule fois
    ranges: list[ArticleRange] = field(default_factory=list)
    binding: list[dict] = field(default_factory=list)   # marchés qui forcent t
    point: dict[int, float] = field(default_factory=dict)  # une solution possible
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "competitor_id": self.competitor_id,
            "n_obs": self.n_obs,
            "feasible": self.feasible,
            "tolerance": self.tolerance,
            "dispersion": self.dispersion,
            "dispersion_source": self.dispersion_source,
            "halflife": self.halflife,
            "n_eff": self.n_eff, "k_eff": self.k_eff, "rare": self.rare,
            "ranges": [r.as_dict() for r in self.ranges],
            "binding": self.binding,
            "point": {str(k): v for k, v in self.point.items()},
            "error": self.error,
        }


def corrected_dispersion(t_star: float, n_obs: int, n_identified: int,
                         safety: float = SAFETY) -> float:
    """Défait le rétrécissement de l'ajustement (voir l'en-tête du module).

    Les k prix libres absorbent une part du bruit, si bien que l'écart minimal
    t* mesuré est plus petit que la dispersion réelle d'un facteur ≈ √(1−k/n).
    Utiliser t* tel quel donne des intervalles étroits qui ne contiennent pas
    les vrais prix.
    """
    if n_obs <= 0:
        return t_star
    # le plancher à 0,05 évite l'explosion quand k ≈ n (autant d'inconnues que
    # d'équations : plus rien n'est réellement contraint)
    shrink = math.sqrt(max(0.05, 1.0 - n_identified / float(n_obs)))
    return t_star / shrink * safety


# ---------------------------------------------------------------------------
def _matrix(obs: Sequence[Observation], arts: Sequence[int]) -> list[list[float]]:
    return [[float(o.qty.get(a, 0.0)) for a in arts] for o in obs]


def _phase1(obs: Sequence[Observation], arts: Sequence[int],
            min_tolerance: float, w: Sequence[float] | None = None
            ) -> tuple[float, list[float]] | None:
    """Plus petit écart relatif rendant les observations mutuellement cohérentes.

    Variables : [p_1 … p_n, t]. On minimise t. `w` élargit la tolérance des
    observations anciennes, qui contraignent alors moins sans disparaître.
    """
    linprog = _linprog()
    Q = _matrix(obs, arts)
    n = len(arts)
    w = list(w) if w is not None else [1.0] * len(obs)

    A, b = [], []
    for row, o, wi in zip(Q, obs, w):
        T = float(o.amount) * float(wi)
        A.append(row + [-T]);            b.append(float(o.amount))
        A.append([-v for v in row] + [-T]); b.append(-float(o.amount))

    res = linprog(c=[0.0] * n + [1.0], A_ub=A, b_ub=b,
                  bounds=[(0, None)] * n + [(min_tolerance, None)],
                  method="highs")
    if not res.success:
        return None
    return float(res.x[-1]), [float(v) for v in res.x[:n]]


def _bounds_at(obs: Sequence[Observation], arts: Sequence[int], t: float,
               w: Sequence[float] | None = None
               ) -> tuple[list[list[float]], list[float]]:
    """Contraintes de la passe 2 : les montants reproduits à t·wᵢ près."""
    Q = _matrix(obs, arts)
    w = list(w) if w is not None else [1.0] * len(obs)
    A, b = [], []
    for row, o, wi in zip(Q, obs, w):
        T = float(o.amount)
        ti = t * float(wi)
        A.append(row);                   b.append(T * (1.0 + ti))
        A.append([-v for v in row]);     b.append(-T * (1.0 - ti))
    return A, b


def solve(observations: Iterable[Observation], articles: Sequence[int],
          competitor_id: int = 0, min_tolerance: float = 0.0,
          dispersion: float | None = None, safety: float = SAFETY,
          halflife: float | None = HALFLIFE_MONTHS,
          today: "dt.date | None" = None) -> Estimate:
    """Intervalles de prix d'un concurrent. Ne lève pas, sauf si scipy manque."""
    obs = [o for o in observations if o.amount and o.amount > 0 and o.qty]
    arts = list(articles)
    est = Estimate(competitor_id=competitor_id, n_obs=len(obs))

    # combien de marchés contraignent chaque article
    counts = {a: sum(1 for o in obs if o.qty.get(a, 0)) for a in arts}
    est.ranges = [ArticleRange(article_id=a, n_obs=counts[a]) for a in arts]
    if not obs:
        est.error = "aucune observation"
        return est

    w = weights(obs, halflife, today)
    est.halflife = float(halflife or 0.0)
    ph1 = _phase1(obs, arts, min_tolerance, w)
    if ph1 is None:
        # Ne devrait pas arriver : p = 0 et t = 1 satisfont toujours les
        # contraintes. Si on est ici, c'est numérique ou une donnée aberrante.
        est.error = ("les montants saisis ne peuvent être expliqués par aucun jeu "
                     "de prix — vérifier les quantités et les montants")
        return est
    t_star, point = ph1
    est.feasible = True
    est.tolerance = t_star
    est.point = {a: p for a, p in zip(arts, point)}

    # Les marchés qui forcent la tolérance : c'est là qu'il faut regarder en cas
    # de saisie douteuse, et c'est le signal d'un éventuel effet acheteur.
    for o, wi in zip(obs, w):
        got = o.total(est.point)
        dev = abs(got - o.amount) / o.amount if o.amount else 0.0
        # une observation ancienne a droit à plus d'écart : elle ne « force »
        # la dispersion que si elle sature SA propre tolérance
        if t_star > 1e-9 and dev >= t_star * wi * 0.999:
            est.binding.append({
                "tender_id": o.tender_id, "label": o.label,
                "amount": o.amount, "expected": got, "deviation": dev,
            })

    n_eff, k_eff = effective_df(obs, arts)
    est.n_eff, est.k_eff = n_eff, k_eff
    est.rare = [a for a in arts if counts[a] == 1]
    if dispersion is None:
        est.dispersion = corrected_dispersion(t_star, n_eff, k_eff, safety)
        est.dispersion_source = "corrigée"
    else:
        est.dispersion = float(dispersion)
        est.dispersion_source = "imposée"

    t_used = est.dispersion * (1.0 + _EPS_REL) + _EPS_ABS
    A, b = _bounds_at(obs, arts, t_used, w)
    linprog = _linprog()
    n = len(arts)
    for k, a in enumerate(arts):
        if counts[a] == 0:
            continue                              # jamais vu : pas d'intervalle
        c = [0.0] * n
        c[k] = 1.0
        lo = linprog(c=c, A_ub=A, b_ub=b, bounds=[(0, None)] * n, method="highs")
        c[k] = -1.0
        hi = linprog(c=c, A_ub=A, b_ub=b, bounds=[(0, None)] * n, method="highs")
        if lo.success:
            est.ranges[k].low = max(0.0, float(lo.x[k]))
        if hi.success:
            est.ranges[k].high = float(hi.x[k])
    return est


def predict(observations: Iterable[Observation], articles: Sequence[int],
            qty: dict[int, float], min_tolerance: float = 0.0,
            dispersion: float | None = None, safety: float = SAFETY,
            halflife: float | None = HALFLIFE_MONTHS,
            today: "dt.date | None" = None) -> dict[str, Any]:
    """Fourchette du montant qu'un concurrent déposerait sur cette composition.

    On optimise la SOMME sur le domaine, pas les articles un par un : sommer
    les intervalles individuels ignorerait les liens entre prix et donnerait
    une fourchette exagérément large.
    """
    obs = [o for o in observations if o.amount and o.amount > 0 and o.qty]
    arts = list(articles)
    out: dict[str, Any] = {"low": None, "high": None, "n_obs": len(obs),
                           "tolerance": 0.0, "dispersion": dispersion or 0.0,
                           "error": "", "unconstrained": []}
    if not obs:
        out["error"] = "aucune observation pour ce concurrent"
        return out

    # Un article demandé que le concurrent n'a jamais chiffré rend la borne
    # haute arbitraire : on le signale au lieu de sortir un chiffre inventé.
    seen = {a for o in obs for a, q in o.qty.items() if q}
    out["unconstrained"] = [a for a, q in qty.items() if q and a not in seen]

    w = weights(obs, halflife, today)
    if dispersion is None:
        ph1 = _phase1(obs, arts, min_tolerance, w)
        if ph1 is None:
            out["error"] = "observations incohérentes"
            return out
        out["tolerance"] = ph1[0]
        n_eff, k_eff = effective_df(obs, arts)
        dispersion = corrected_dispersion(ph1[0], n_eff, k_eff, safety)
        out["dispersion"] = dispersion

    t_used = dispersion * (1.0 + _EPS_REL) + _EPS_ABS
    A, b = _bounds_at(obs, arts, t_used, w)
    linprog = _linprog()
    n = len(arts)
    c = [float(qty.get(a, 0.0)) for a in arts]
    lo = linprog(c=c, A_ub=A, b_ub=b, bounds=[(0, None)] * n, method="highs")
    hi = linprog(c=[-v for v in c], A_ub=A, b_ub=b,
                 bounds=[(0, None)] * n, method="highs")
    if lo.success:
        out["low"] = float(lo.fun)
    if hi.success:
        out["high"] = float(-hi.fun)
    return out
