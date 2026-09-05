"""
Jeu de tests du dictionnaire et de l'appariement.

Les intitulés « réels » proviennent d'avis effectivement publiés sur TUNEPS
(observés via l'API publique) ; les variantes fautives simulent les erreurs de
saisie fréquentes des acheteurs publics.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuneps.matcher import default_matcher, normalize  # noqa: E402

M = default_matcher()

# --- intitulés réels qui DOIVENT matcher ----------------------------------
DOIT_MATCHER = [
    "Acquisition des drapeaux, des guirlandes et des accessoires",
    "Achat drapeaux",
    "ENCRE SPECIAL LINGE ET DRAPEAUX",
    "CONSULTATION D’ACHETER DES DRAPEAUX DE LA RÉPUBLIQUE TUNISIENNE",
    "Acquisition porte drapeaux",
    "Fourniture de drapeaux officiels au profit de la Présidence",
    "drapeaux et guirlande",
    "Drapeaux Tunisiee",
    "اقتناء أعلام وزينة لتزيين الشوارع",
    "تزيين شوارع المدينة بالرايات واللافتات",
    "Supply of national flags and banners",
    "Impression de banderoles publicitaires",
    "Fourniture et pose d'oriflammes pour la fête nationale",
    "Acquisition de kakemonos et roll-up",
]

# --- fautes de frappe / graphies déviantes --------------------------------
FAUTES = [
    "Acquisition de drapaux nationaux",         # drapeaux -> drapaux
    "achat de guirelandes decoratives",         # guirlandes -> guirelandes
    "fourniture d'oriflame publicitaire",       # oriflamme -> oriflame
    "impression de banderolle",                 # banderole -> banderolle
    "acquisition de baniere pour la commune",   # banniere -> baniere
    "DRAPEAUXX ET ACCESSOIRES",                 # doublement de lettre
    "اقتناء اعلام وطنية",                        # أعلام sans hamza
]

# --- intitulés qui NE doivent PAS matcher ---------------------------------
NE_DOIT_PAS = [
    "Travaux d’aménagement des carrefours dans la Commune de Djerba Houmt Souk",
    "Travaux de protection contre les inondations de plusieurs villes",
    "Acquisition de mobiliers de bureau pour l’année 2017",
    "TRAVAUX D’ECLAIRAGE PUBLIC AWLED ALI COMMUNE ENNOUR KASSERINE",
    "Acquisition de matériels informatique et d’affichage",
    "أشغال تهيئة المفترقات ببلدية جربة حومة السوق",
    "REAMENAGEMENT DU PISCINE MUNICIPAL DE METLAOUI",
    "Confection et l’Installation des produits métalliques & Gardes Corps en Inox",
]


def run() -> int:
    fails: list[str] = []

    for t in DOIT_MATCHER:
        r = M.match(t)
        if not r.matched:
            fails.append(f"FAUX NÉGATIF (réel)  : {t}")

    for t in FAUTES:
        r = M.match(t)
        if not r.matched:
            fails.append(f"FAUX NÉGATIF (faute) : {t}")

    for t in NE_DOIT_PAS:
        r = M.match(t)
        if r.matched:
            fails.append(f"FAUX POSITIF         : {t}  -> {r.summary()}")

    # normalisation
    assert normalize("Drapeaux Tunisiée") == "drapeaux tunisiee", normalize("Drapeaux Tunisiée")
    assert normalize("أَعْلاَم") == normalize("اعلام"), (normalize("أَعْلاَم"), normalize("اعلام"))
    assert normalize("  ROLL-UP   ") == "roll up"

    total = len(DOIT_MATCHER) + len(FAUTES) + len(NE_DOIT_PAS)
    print(f"{total - len(fails)}/{total} cas conformes")
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
