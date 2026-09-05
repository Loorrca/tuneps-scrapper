"""
Normalisation multilingue (FR / AR / EN) + appariement tolérant aux fautes.

Le portail TUNEPS est saisi à la main par des centaines d'acheteurs publics :
les intitulés contiennent des fautes de frappe, des accents manquants,
des diacritiques arabes, des espaces multiples, du CAPS LOCK.
Ce module ramène tout à une forme canonique puis applique :

  1. un match exact sur la forme normalisée  (rapide, sûr)
  2. un match flou par fenêtre de tokens     (rattrape les fautes)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# --- Arabe ----------------------------------------------------------------
_AR_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_AR_MAP = {
    "آ": "ا",  # آ -> ا
    "أ": "ا",  # أ -> ا
    "إ": "ا",  # إ -> ا
    "ٱ": "ا",  # ٱ -> ا
    "ة": "ه",  # ة -> ه
    "ى": "ي",  # ى -> ي
    "ک": "ك",  # ک -> ك
    "ی": "ي",  # ی -> ي
}

_PUNCT = re.compile(r"[^\w\s؀-ۿ]+", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    """Forme canonique : minuscules, sans accents latins ni diacritiques arabes."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    # retire toutes les marques combinantes : accents latins ET diacritiques arabes
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = _AR_DIACRITICS.sub("", s)
    s = "".join(_AR_MAP.get(c, c) for c in s)
    s = s.replace("œ", "oe").replace("æ", "ae")
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split(" ") if t]


# --- frontières de mots ---------------------------------------------------
# L'arabe est agglutinant : « الأعلام », « بالأعلام », « والأعلام » sont tous
# la même racine « أعلام ».  Sans traitement des préfixes, une recherche par
# sous-chaîne confond « أعلام » (drapeaux) avec « إعلامية » (informatique) —
# un piège majeur sur TUNEPS, où « معدات إعلامية » revient sans cesse.
_AR_PREFIXES = ("وبال", "فبال", "وال", "فال", "بال", "كال", "لل", "ال",
                "و", "ف", "ب", "ك", "ل")


def _is_arabic(s: str) -> bool:
    return any("؀" <= c <= "ۿ" for c in s)


def strip_ar_prefix(token: str) -> list[str]:
    """Formes candidates d'un token arabe une fois ses préfixes courants retirés."""
    out = [token]
    for p in _AR_PREFIXES:
        if token.startswith(p) and len(token) - len(p) >= 3:
            out.append(token[len(p):])
    return out


def token_matches(token: str, term: str) -> bool:
    """Le token porte-t-il exactement ce terme (pluriels et préfixes admis) ?"""
    if token == term:
        return True
    if _is_arabic(term):
        return any(c == term for c in strip_ar_prefix(token))
    # latin : tolère le pluriel dans un sens comme dans l'autre
    return token in (term + "s", term + "x") or term in (token + "s", token + "x")


# --------------------------------------------------------------------------


@dataclass
class Hit:
    term: str           # le mot-clé du dictionnaire
    tier: str           # "core" | "secondary" | "context"
    found: str          # ce qui a réellement été trouvé dans le texte
    exact: bool
    ratio: float        # 1.0 si exact


@dataclass
class MatchResult:
    matched: bool = False
    confidence: str = "none"        # "high" | "review" | "none"
    score: float = 0.0
    hits: list[Hit] = field(default_factory=list)
    excluded_by: str | None = None
    demoted_by: str | None = None

    @property
    def terms(self) -> list[str]:
        seen, out = set(), []
        for h in self.hits:
            if h.term not in seen:
                seen.add(h.term)
                out.append(h.term)
        return out

    def summary(self) -> str:
        parts = []
        for h in self.hits:
            parts.append(h.term if h.exact else f"{h.term}~{h.found}")
        return ", ".join(parts)


def _fuzzy_window_match(hay_tokens: list[str], needle: str, threshold: float) -> tuple[bool, str, float]:
    """
    Compare `needle` (déjà normalisé, éventuellement multi-mots) à toutes les
    fenêtres glissantes de même longueur en tokens dans le texte.
    Retourne (trouvé, extrait, ratio).
    """
    n_words = needle.count(" ") + 1
    best_ratio, best_txt = 0.0, ""
    # Seuil de longueur minimal avant d'autoriser le flou.  L'arabe est plus
    # exigeant : ses mots sont courts et une seule lettre change le sens
    # (« الزينة » décoration vs « الخزينة » trésorerie).
    min_len = 6 if _is_arabic(needle) else 5
    if len(needle) < min_len:
        return False, "", 0.0
    for i in range(0, max(1, len(hay_tokens) - n_words + 1)):
        window = " ".join(hay_tokens[i:i + n_words])
        if not window:
            continue
        # garde-fou de longueur : évite de comparer "flag" à "fabrication"
        if abs(len(window) - len(needle)) > max(3, len(needle) * 0.4):
            continue
        r = SequenceMatcher(None, needle, window).ratio()
        if r > best_ratio:
            best_ratio, best_txt = r, window
    return best_ratio >= threshold, best_txt, best_ratio


class Matcher:
    """Compile le dictionnaire une fois, puis évalue des intitulés."""

    def __init__(
        self,
        core: list[str],
        secondary: list[str],
        context: list[str],
        exclusions: list[str],
        demote: list[str] | None = None,
        textile_markers: list[str] | None = None,
        fuzzy_threshold: float = 0.86,
        fuzzy_enabled: bool = True,
    ):
        self.core = sorted({normalize(t) for t in core if normalize(t)}, key=len, reverse=True)
        self.secondary = sorted({normalize(t) for t in secondary if normalize(t)}, key=len, reverse=True)
        self.context = sorted({normalize(t) for t in context if normalize(t)}, key=len, reverse=True)
        self.exclusions = [normalize(t) for t in exclusions if normalize(t)]
        self.demote = [normalize(t) for t in (demote or []) if normalize(t)]
        self.textile = [normalize(t) for t in (textile_markers or []) if normalize(t)]
        self.threshold = fuzzy_threshold
        self.fuzzy = fuzzy_enabled

    # ------------------------------------------------------------------
    def match(self, *texts: str | None) -> MatchResult:
        raw = " | ".join(t for t in texts if t)
        hay = normalize(raw)
        if not hay:
            return MatchResult()

        for ex in self.exclusions:
            if ex and ex in hay:
                return MatchResult(excluded_by=ex)

        hay_tokens = hay.split(" ")
        res = MatchResult()

        for tier, terms in (("core", self.core), ("secondary", self.secondary), ("context", self.context)):
            for term in terms:
                if " " in term:
                    # expression : la sous-chaîne suffit, les mots font déjà frontière
                    if term in hay:
                        res.hits.append(Hit(term, tier, term, True, 1.0))
                        continue
                else:
                    # mot simple : exiger une vraie frontière de mot, sinon
                    # « أعلام » (drapeaux) se confond avec « إعلامية » (informatique)
                    # et « علم » avec « معلم ».
                    found_tok = next((t for t in hay_tokens if token_matches(t, term)), None)
                    if found_tok:
                        res.hits.append(Hit(term, tier, found_tok, True, 1.0))
                        continue
                if self.fuzzy and tier != "context":
                    ok, found, ratio = _fuzzy_window_match(hay_tokens, term, self.threshold)
                    if ok:
                        res.hits.append(Hit(term, tier, found, False, round(ratio, 3)))

        n_core = sum(1 for h in res.hits if h.tier == "core")
        n_sec = sum(1 for h in res.hits if h.tier == "secondary")
        n_ctx = sum(1 for h in res.hits if h.tier == "context")

        res.score = 3.0 * n_core + 1.0 * n_sec + 0.3 * n_ctx

        if n_core:
            res.matched, res.confidence = True, "high"
        elif n_sec >= 2 or (n_sec >= 1 and n_ctx >= 1):
            res.matched, res.confidence = True, "review"

        # Un contexte « rétrogradant » (signalétique métallique, enseigne
        # lumineuse, tampons) ne supprime pas l'avis : il le fait basculer
        # en « à vérifier » pour qu'il reste visible sans polluer les alertes.
        has_textile = any(
            t in hay or any(token_matches(tok, t) for tok in hay_tokens)
            for t in self.textile
        )
        if res.matched and res.confidence == "high" and not has_textile:
            for d in self.demote:
                if d and (d in hay or any(token_matches(t, d) for t in hay_tokens)):
                    res.confidence, res.demoted_by = "review", d
                    break
        return res


def default_matcher(cfg: dict | None = None) -> Matcher:
    from . import keywords as K

    cfg = cfg or {}
    extra_core = cfg.get("extra_keywords", []) or []
    extra_excl = cfg.get("extra_exclusions", []) or []
    return Matcher(
        core=K.all_core() + list(extra_core),
        secondary=K.SECONDARY,
        context=K.CONTEXT,
        exclusions=K.EXCLUSIONS + list(extra_excl),
        demote=K.DEMOTE + list(cfg.get("extra_demote", []) or []),
        textile_markers=K.TEXTILE_MARKERS,
        fuzzy_threshold=float(cfg.get("fuzzy_threshold", 0.86)),
        fuzzy_enabled=bool(cfg.get("fuzzy", True)),
    )
