"""
Dictionnaire de mots-clés pour la détection d'appels d'offres
« drapeaux / banderoles / oriflammes / guirlandes / impression textile ».

Deux niveaux :

  CORE       -> un seul match suffit pour déclencher une alerte.
  SECONDAIRE -> signal plus faible (métier de l'imprimerie / textile publicitaire).
                Il faut au moins DEUX termes secondaires, ou un secondaire
                combiné à un terme de contexte, pour lever une alerte
                « à vérifier ».
  EXCLUSIONS -> termes qui annulent un match manifestement hors-sujet
                (ex : « drapeau » dans un contexte informatique).

Toutes les chaînes sont comparées APRÈS normalisation
(minuscules, accents retirés, diacritiques arabes retirés) —
voir tuneps/matcher.py. Écrivez-les donc en clair, la normalisation
est appliquée automatiquement au chargement.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# NOYAU — français
# --------------------------------------------------------------------------
CORE_FR = [
    # drapeaux
    "drapeau",
    "drapeaux",
    "porte-drapeau",
    "porte drapeau",
    "mat de drapeau",
    "mats de drapeaux",
    "hampe",
    "hampes",
    "pavoisement",
    "pavoiser",
    "pavois",
    "fanion",
    "fanions",
    # oriflammes / voiles
    "oriflamme",
    "oriflammes",
    "beach flag",
    "beachflag",
    "voile publicitaire",
    "drapeau plume",
    # banderoles / bannieres
    "banderole",
    "banderoles",
    "banniere",
    "bannieres",
    "calicot",
    "calicots",
    "kakemono",
    "kakemonos",
    "roll-up",
    "roll up",
    "rollup",
    "x-banner",
    "totem publicitaire",
    # guirlandes / decoration evenementielle
    "guirlande",
    "guirlandes",
    "fanions decoratifs",
    "decoration des rues",
    "decoration de la ville",
    "decoration des avenues",
    "embellissement des rues",
]

# --------------------------------------------------------------------------
# NOYAU — arabe
# --------------------------------------------------------------------------
CORE_AR = [
    # أعلام / رايات
    "علم",
    "أعلام",
    "اعلام",
    "راية",
    "رايات",
    "بيرق",
    "بيارق",
    "سارية",
    "سواري",
    "حامل العلم",
    "ترفيع العلم",
    "تزويق",
    # لافتات / معلقات
    "لافتة",
    "لافتات",
    "معلقات",
    "معلقة",
    "يافطة",
    "يافطات",
    "شعارات",
    # زينة
    "زينة",
    "تزيين",
    "زخرفة",
    "تزيين الشوارع",
    "تزويق الشوارع",
    # formulations réellement employées par les acheteurs tunisiens
    "أشرطة زينة",
    "اشرطة زينة",
    "شرائط زينة",
    "شرائط الزينة",
    "أعلام زينة",
    "اعلام زينة",
    "أعلام الزينة",
    "مواد زينة",
    "مواد الزينة",
    "معالم زينة",
    "معالم الزينة",
    "معاليم الزينة",
    "علامات زينة",
    "علامات الزينة",
    "لوازم الزينة",
    "الراية الوطنية",
    "راية وطنية",
    "أعمدة أعلام",
    "اعمدة اعلام",
]

# --------------------------------------------------------------------------
# NOYAU — anglais (certains avis sont publiés en anglais)
# --------------------------------------------------------------------------
CORE_EN = [
    "flag",
    "flags",
    "flagpole",
    "banner",
    "banners",
    "bunting",
    "pennant",
    "pennants",
    "garland",
    "garlands",
    "streamer",
    "streamers",
    "feather flag",
]

# --------------------------------------------------------------------------
# SECONDAIRE — le métier (impression / textile publicitaire)
# --------------------------------------------------------------------------
SECONDARY = [
    "serigraphie",
    "impression sur tissu",
    "impression textile",
    "impression numerique",
    "impression grand format",
    "sublimation",
    "broderie",
    "tissu polyester",
    "signaletique",
    "support publicitaire",
    "supports publicitaires",
    "articles publicitaires",
    "support de communication",
    "supports de communication",
    "habillage",
    "gadgets publicitaires",
    "objets publicitaires",
    "طباعة",
    "طباعة رقمية",
    "الطباعة على القماش",
    "إشهار",
    "إشهارية",
    "لوحات إشهارية",
    "نسيج",
    "قماش",
    "تطريز",
    "printing",
    "digital printing",
    "textile printing",
    "screen printing",
    "signage",
    "promotional items",
]

# --------------------------------------------------------------------------
# CONTEXTE — renforce un match secondaire (jamais déclencheur seul)
# --------------------------------------------------------------------------
CONTEXT = [
    "fete nationale",
    "fetes nationales",
    "celebration",
    "manifestation",
    "festival",
    "ceremonie",
    "commemoration",
    "evenement",
    "evenementiel",
    "fourniture et pose",
    "acquisition",
    "achat",
    "العيد الوطني",
    "الاحتفال",
    "الاحتفالات",
    "تظاهرة",
    "مهرجان",
    "اقتناء",
    "شراء",
]

# --------------------------------------------------------------------------
# EXCLUSIONS — annulent un match noyau lorsqu'elles apparaissent
#              dans le même intitulé (faux amis).
# --------------------------------------------------------------------------
EXCLUSIONS = [
    "drapeau rouge",          # signalisation routiere / chantier
    "flag ip",                # informatique
    "guirlande lumineuse led",  # eclairage public -> souvent electricien, pas textile
    "guirlandes lumineuses",
    "bannière web",
    "banniere web",
    "web banner",
    # arabe : « إعلامية / الإعلام » = informatique / médias, jamais des drapeaux
    "اعلامية",
    "إعلامية",
    "الاعلامية",
    "وسائل الاعلام",
    "وسائل الإعلام",
    "الاعلام والاتصال",
    "الإعلام والاتصال",
    "تكنولوجيا المعلومات",
    "أعلامية",
    "الأعلامية",
    "معدات اعلامية",
    "لوازم اعلامية",
    "تجهيزات اعلامية",
    "شبكة الاعلامية",
    # « نباتات زينة » = plantes ornementales : horticulture, pas textile
    "نباتات زينة",
    "نباتات الزينة",
    "نبتات زينة",
    "نبتات الزينة",
    "نبتة زينة",
    "اشجار زينة",
    "أشجار زينة",
    "اشجار الزينة",
    "أشجار الزينة",
    "اجشار زينة",
    "نباتات للزينة",
    "نباتات زينه",
    "plantes ornementales",
    "plante ornementale",
    "plantes artificielles",
    "plantes d ornement",
    "plantes d interieur",
    "bacs a fleurs",
    "arbres d hornements",
    "espaces verts",
    "gazon",
    "حجارة زينة",
    "حجارة الزينة",
    "galet de decoration",
]

# --------------------------------------------------------------------------
# MARQUEURS TEXTILE — leur présence annule toute rétrogradation : si l'avis
# parle explicitement de tissu, c'est bien le métier, même s'il mentionne
# aussi des tampons ou de la signalétique.
# --------------------------------------------------------------------------
TEXTILE_MARKERS = [
    "tissu",
    "textile",
    "polyester",
    "bache",
    "baches",
    "toile",
    "impression sur tissu",
    "impression textile",
    "قماش",
    "القماش",
    "نسيج",
    "الباش",
    "خياطة",
]

# --------------------------------------------------------------------------
# RÉTROGRADATION — n'annulent pas la détection, mais font passer un avis de
# « correspondance forte » à « à vérifier ».  Ce sont des contextes où le mot
# noyau existe bien, mais désigne souvent un autre métier : signalétique
# métallique, enseignes lumineuses, tampons et cachets.
# --------------------------------------------------------------------------
DEMOTE = [
    # lumineux -> electricien
    "lumineuse",
    "lumineuses",
    "lumineux",
    "illumination",
    "illuminations",
    "eclairage",
    "ضوئية",
    "الضوئية",
    "مضيئة",
    "كهرباء زينة",
    # signalisation routiere / plaques -> metal
    "signalisation",
    "signaletique routiere",
    "panneau de signalisation",
    "panneaux de signalisation",
    "plaque d identification",
    "plaques d identification",
    "enseigne",
    "enseignes",
    "توجيهية",
    "ارشادية",
    "إرشادية",
    "ارشاد",
    "مرورية",
    "تشوير",
    "من معدن",
    "معدنية",
    "لوحات تعريفية",
    # cachets / tampons -> imprimerie administrative
    "cachet",
    "cachets",
    "tampon",
    "tampons",
    "timbre",
    "timbres",
    "اختام",
    "أختام",
    "طوابع",
]

# --------------------------------------------------------------------------
# RACINES (stems) — utilisées pour les requêtes « like %stem% » côté serveur.
# Volontairement tronquées pour absorber les fautes de frappe usuelles
# (drapeaux / drapeau / drapaux / drapeauX...).
# --------------------------------------------------------------------------
SERVER_STEMS = [
    # francais
    "drape",
    "drapp",
    "oriflam",
    "orifflam",
    "banderol",
    "bandrol",
    "bannier",
    "banier",
    "guirland",
    "guirlend",
    "girland",
    "fanion",
    "kakemono",
    "roll-up",
    "rollup",
    "calicot",
    "pavois",
    "hampe",
    "beach flag",
    "serigraph",
    "impression sur tissu",
    "impression textile",
    "sublimation",
    "broderie",
    # arabe
    "أعلام",
    "اعلام",
    "راية",
    "رايات",
    "بيرق",
    "لافتة",
    "لافتات",
    "زينة",
    "تزيين",
    "سارية",
    "معلقات",
    "اشرطة زينة",
    "أشرطة زينة",
    "شرائط زينة",
    "الراية الوطنية",
    "pavois",
    # anglais
    "flag",
    "banner",
    "bunting",
    "pennant",
    "garland",
]


def all_core() -> list[str]:
    return CORE_FR + CORE_AR + CORE_EN
