"""Bout-en-bout hors ligne : parsing API -> matching -> base -> rapport HTML."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuneps.client import _from_bid, _from_shop  # noqa: E402
from tuneps.matcher import default_matcher  # noqa: E402
from tuneps.report import render_html, render_text  # noqa: E402
from tuneps.store import Store  # noqa: E402

# Réponses brutes réellement renvoyées par l'API TUNEPS (extraits).
BID_ROWS = [{
    "bdRecvEndDt": "2026-10-08 11:00:00.0",
    "bidInstNm": "Commune de Djerba Houmt Souk",
    "bidNmEn": "Travaux d’aménagement des carrefours",
    "publicDt": "2026-09-02 21:48:30.245856", "publicYn": "Y", "bidModSeq": "00",
    "bidNo": "20260900572", "bidNmAr": "اشغال تهيئة المفترقات ببلدية جربة حومة السوق",
    "bidNmFr": "Travaux d’aménagement des carrefours", "epBidMasterId": 134654,
}, {
    "bdRecvEndDt": "2026-09-25 10:00:00.0", "bidInstNm": "Présidence du Gouvernement",
    "bidNmEn": "", "publicDt": "2026-09-01 09:00:00.0", "publicYn": "Y",
    "bidModSeq": "00", "bidNo": "20260900148",
    "bidNmAr": "إقتناء أعلام وطنية", "bidNmFr": "Fourniture de drapeaux officiels",
    "epBidMasterId": 134600,
}]

SHOP_ROWS = [{
    "publicDt": "2026-09-02 11:14:48.714911", "spShopMasterId": 90001, "shopModSeq": "00",
    "instNm": "Commune de Sousse", "shopNmEn": "",
    "shopNmFr": "Acquisition drapeaux et banderoles",
    "spRecvEndDt": "2026-09-19 09:00:00.0", "shopNo": "S20260901896",
    "shopNmAr": "اقتناء أعلام زينة و لافتات",
}, {
    "publicDt": "2026-09-02 12:00:00.0", "spShopMasterId": 90002, "shopModSeq": "00",
    "instNm": "Commune de Nabeul", "shopNmEn": "",
    "shopNmFr": "Acquisition des plantes d'ornement",
    "spRecvEndDt": "2026-09-30 09:00:00.0", "shopNo": "S20260901897",
    "shopNmAr": "إقتناء نباتات زينة",
}]


def run() -> int:
    fails: list[str] = []
    notices = [_from_bid(r) for r in BID_ROWS] + [_from_shop(r) for r in SHOP_ROWS]

    # --- parsing
    b = notices[1]
    if b.url != "https://www.tuneps.tn/portail/offres/details/134600/20260900148":
        fails.append(f"URL de détail incorrecte : {b.url}")
    if b.uid != "ao:20260900148:00":
        fails.append(f"uid incorrect : {b.uid}")
    s = notices[2]
    if s.source != "consultation" or s.buyer != "Commune de Sousse":
        fails.append("mapping consultation incorrect")
    if s.deadline_at != "2026-09-19 09:00:00":
        fails.append(f"échéance mal tronquée : {s.deadline_at}")

    # --- matching
    m = default_matcher()
    verdicts = {n.number: m.match(*n.titles) for n in notices}
    if verdicts["20260900572"].matched:
        fails.append("faux positif : travaux d'aménagement des carrefours")
    if verdicts["20260900148"].confidence != "high":
        fails.append("drapeaux officiels aurait dû être « forte »")
    if verdicts["S20260901896"].confidence != "high":
        fails.append("drapeaux et banderoles aurait dû être « forte »")
    if verdicts["S20260901897"].matched:
        fails.append("faux positif : plantes d'ornement")

    # --- persistance + déduplication
    with tempfile.TemporaryDirectory() as td:
        st = Store(Path(td) / "t.db")
        first = [st.record(n, v.confidence, v.score, v.terms)
                 for n, v in ((n, verdicts[n.number]) for n in notices) if v.matched]
        if first != [True, True]:
            fails.append(f"insertion initiale inattendue : {first}")
        again = [st.record(n, verdicts[n.number].confidence, 0, [])
                 for n in notices if verdicts[n.number].matched]
        if any(again):
            fails.append("un avis déjà connu a été ré-inséré (déduplication cassée)")

        rows = st.pending()
        if len(rows) != 2:
            fails.append(f"{len(rows)} avis en attente au lieu de 2")

        # --- rendu
        html_doc = render_html(rows, subtitle="test")
        for needle in ("Fourniture de drapeaux officiels", "Acquisition drapeaux et banderoles",
                       "portail/offres/details/134600", 'dir="rtl"'):
            if needle not in html_doc:
                fails.append(f"absent du HTML : {needle}")
        if "<script" in html_doc.lower():
            fails.append("le HTML ne doit pas contenir de script")
        txt = render_text(rows)
        if "drapeaux" not in txt.lower():
            fails.append("rendu texte vide")

        st.mark_notified([r["uid"] for r in rows])
        if st.pending():
            fails.append("mark_notified n'a pas vidé la file d'attente")

        # rapport vide -> message neutre, pas de plantage
        if "Aucun nouvel avis" not in render_html([]):
            fails.append("rapport vide mal rendu")
        st.close()

    print(f"bout-en-bout : {'OK' if not fails else str(len(fails)) + ' échec(s)'}")
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
