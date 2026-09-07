"""
Identité des concurrents : reconnaître la même société sous des graphies
différentes.

TUNEPS publie les raisons sociales en texte libre, saisies par des agents
différents : « STE ALPHA TEXTILE SARL », « Société Alpha-Textile »,
« ALPHA TEXTILES » désignent la même entreprise. Sans rapprochement, chacune
devient un concurrent distinct et les observations se dispersent entre trois
dossiers dont aucun n'a assez d'équations pour être exploitable.

Le rapprochement se fait en deux temps :

1. **Normalisation** — majuscules, accents retirés, ponctuation supprimée,
   formes juridiques et mots vides écartés. « STE ALPHA TEXTILE SARL » et
   « Société Alpha-Textile » donnent tous deux « ALPHA TEXTILE ».
2. **Rapprochement approché** — si la forme normalisée n'est connue ni comme
   concurrent ni comme alias, on cherche la plus proche par distance
   d'édition, avec deux garde-fous : un seuil élevé, et l'obligation de
   partager au moins un mot significatif. Sans le second, « ALPHA TEXTILE »
   et « ALPHA TEXTILES SUD » se confondraient alors que ce sont peut-être
   deux sociétés.

Le rapprochement automatique se trompera parfois. C'est pourquoi l'interface
expose la fusion manuelle : c'est le filet, et il est indispensable — un
rapprochement raté ne se voit pas, il dilue seulement les données.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Formes juridiques et mots de liaison : présents ou non selon qui saisit,
# donc sans valeur discriminante.
STOP = {
    "STE", "SOCIETE", "SOC", "SARL", "SA", "SUARL", "SNC", "SPA", "EURL",
    "ETS", "ETABLISSEMENT", "ETABLISSEMENTS", "ENTREPRISE", "ENT", "CIE",
    "COMPAGNIE", "GROUPE", "GROUP", "HOLDING", "SASU", "SAS", "SARLU",
    "DE", "DU", "DES", "LA", "LE", "LES", "ET", "AND", "EL", "AL",
    "TUNISIE", "TUNISIENNE", "TUNISIEN",
    # équivalents arabes courants
    "شركة", "مؤسسة", "ش", "م", "ذ",
}

# seuil de rapprochement approché : au-dessus, on considère que c'est la même
# société. Volontairement élevé — un faux positif fusionne deux concurrents,
# ce qui est pire qu'un faux négatif que l'utilisateur peut fusionner à la main.
THRESHOLD = 0.88


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def normalize_name(raw: str) -> str:
    """Forme canonique servant de clé de rapprochement."""
    s = _strip_accents(str(raw or "")).upper()
    s = re.sub(r"[^0-9A-Z؀-ۿ]+", " ", s)
    toks = [t for t in s.split() if t and t not in STOP]
    # tout retirer serait pire que ne rien retirer : « STE TUNISIE » doit
    # rester distinguable de « STE SARL » plutôt que de devenir vide.
    if not toks:
        toks = [t for t in s.split() if t]
    return " ".join(toks)


def tokens(norm: str) -> set[str]:
    """Mots significatifs : au moins 3 caractères, pour ignorer les initiales."""
    return {t for t in norm.split() if len(t) >= 3}


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def match_name(norm: str, candidates: dict[str, int],
               threshold: float = THRESHOLD) -> tuple[int | None, float, str]:
    """Rattache une forme normalisée à un concurrent existant.

    `candidates` : forme normalisée -> identifiant de concurrent (noms
    canoniques ET alias confondus).

    Retourne (identifiant ou None, score, forme rapprochée).
    """
    if not norm:
        return None, 0.0, ""
    if norm in candidates:
        return candidates[norm], 1.0, norm

    mine = tokens(norm)
    best_id, best_score, best_key = None, 0.0, ""
    for key, cid in candidates.items():
        score = similarity(norm, key)
        if score <= best_score or score < threshold:
            continue
        # garde-fou : au moins un mot significatif en commun. Sans lui, deux
        # raisons sociales courtes et proches par les lettres seraient
        # fusionnées à tort.
        theirs = tokens(key)
        if mine and theirs and not (mine & theirs):
            continue
        best_id, best_score, best_key = cid, score, key
    return best_id, best_score, best_key
