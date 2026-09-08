"""
Résultats publiés par TUNEPS : lecture du payload réel, dédoublonnage par
société, persistance, et bout-en-bout HTTP sur /api/result.

Aucun appel réseau : les réponses du portail sont rejouées telles qu'elles ont
été capturées, pour que le test échoue si la lecture des champs change.
"""

from __future__ import annotations

import re
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuneps import results as results_mod  # noqa: E402
from tuneps.results import Bidder, Result, ResultsClient, _bidder_from_shop  # noqa: E402
from tuneps.store import Store  # noqa: E402
from tuneps.client import _from_shop  # noqa: E402
from tests.test_pipeline import SHOP_ROWS  # noqa: E402

# --- une ligne du portail, champs tels qu'ils arrivent -----------------------
ROW_RETENU = {
    "bizRegNm": "SOCIETE ALPHA TEXTILE",
    "bizRegNo": "1234567A",
    "totalShopPriceCorr": "12500.500",
    "totalShopPriceDcExch": "12900.000",
    "rankDep": "1",
    "cdNmStrFr": "Retenu",
    "ineligibleCd": "00",
    "ineligibleReason": "-",
    "spRecvDtReal": "2026-08-12 10:22:31.000000",
    "shopCls": "1",
}
ROW_ECARTE = {
    "bizRegNm": "BETA CONFECTION",
    "bizRegNo": "7654321B",
    "totalShopPriceCorr": None,
    "totalShopPriceDcExch": "11800.000",
    "rankDep": "2",
    "cdNmStrFr": "Non retenu",
    "ineligibleCd": "13",
    "ineligibleReason": "Dossier administratif incomplet",
    "spRecvDtReal": "2026-08-12 09:01:00.000000",
    "shopCls": "1",
}
# la même société sur un SECOND LOT : c'est une observation distincte, avec sa
# propre composition et son propre montant. Elle doit être conservée.
ROW_RETENU_LOT2 = dict(ROW_RETENU, rankDep="1", totalShopPriceCorr="4000.000",
                       shopCls="2")

# une ligne d'appel d'offres, champs tels que ranking/rankLot les renvoie
ROW_AO = {
    "bizRegNm": "SOCIETE MAISON MADAME BOUZID BEN ALI",
    "bizRegNo": "1333127X",
    "totalBidPriceDcExch": 103123.02,
    "rank": 1, "rk": 1, "bidCls": "1",
    "cdPfFinaFr": "Retenu", "cdPfTechFr": "Retenu",
    "cdNmStrFr": "Retenu pour l evaluation",
    "ineligibleCd": "00", "finaResultReason": " ", "techResultReason": " ",
    "bdRecvDt": "2024-11-26 10:47:02.0",
}
ROW_AO_2 = dict(ROW_AO, bizRegNm="COTUFAD", bizRegNo="0879426D",
                totalBidPriceDcExch=105600, rank=2, bidCls="2",
                cdPfFinaFr="Non retenu",
                finaResultReason="ليس بالعرض الاقل سعرا")


class StubClient:
    """Rejoue les réponses du portail sans toucher au réseau."""
    timeout = 5

    def __init__(self, payloads: dict):
        self.payloads = payloads
        self.seen: list[str] = []
        self.session = self

    def get(self, url, timeout=None):  # noqa: ARG002
        self.seen.append(url)
        key = next((k for k in self.payloads if k in url), None)
        if key is None:
            raise RuntimeError(f"point d'entrée non simulé : {url}")
        return _Resp(self.payloads[key])

    def post(self, url, json=None, timeout=None):  # noqa: A002, ARG002
        """Le portail attend les paramètres dans l'URL et un corps JSON, même vide."""
        self.seen.append(url)
        if json is None:
            return _Resp(None, status=415)      # comme le vrai serveur
        key = next((k for k in self.payloads if k in url), None)
        if key is None:
            return _Resp(None, status=404)
        return _Resp(self.payloads[key])


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"payload": self._payload}


def run() -> int:
    fails: list[str] = []

    # --- lecture d'une ligne réelle
    b = _bidder_from_shop(ROW_RETENU)
    if b.lot != "1":
        fails.append(f"lot de consultation mal lu : {b.lot!r}")
    if b.company != "SOCIETE ALPHA TEXTILE":
        fails.append(f"société mal lue : {b.company!r}")
    if b.price != 12500.5:
        fails.append(f"prix corrigé non retenu : {b.price!r} (attendu 12500.5)")
    if b.rank != 1:
        fails.append(f"rang mal lu : {b.rank!r}")
    if b.retained is not True:
        fails.append(f"« Retenu » non reconnu : {b.retained!r}")
    if b.reason != "":
        fails.append(f"le motif « - » aurait dû être vidé : {b.reason!r}")
    if b.submitted_at != "2026-08-12 10:22:31":
        fails.append(f"date de dépôt mal tronquée : {b.submitted_at!r}")

    e = _bidder_from_shop(ROW_ECARTE)
    if e.retained is not False:
        fails.append(f"« Non retenu » non reconnu : {e.retained!r}")
    if e.price != 11800.0:
        fails.append(f"repli sur le prix non corrigé absent : {e.price!r}")
    if e.reason != "Dossier administratif incomplet":
        fails.append(f"motif d'écartement perdu : {e.reason!r}")

    # statut absent : on ne doit pas inventer un « retenu »
    muet = _bidder_from_shop({"bizRegNm": "GAMMA", "rankDep": "1"})
    if muet.retained is not None:
        fails.append(f"statut inconnu transformé en {muet.retained!r}")

    # --- consultation : drapeaux + dédoublonnage
    rc = ResultsClient(client=StubClient({
        "checkResultat": "Y",
        "cehckWinner": "Y",
        # l'ordre compte : la bonne ligne d'ALPHA arrive en premier, donc
        # « garder la dernière vue » se ferait repérer par le tri attendu.
        "openProgressSupCls": [ROW_RETENU, ROW_ECARTE, ROW_RETENU_LOT2],
        "spFailResult": [],
    }))
    res = rc.for_notice("consultation", "20260812345", "00")
    if not (res.published and res.winner_declared):
        fails.append(f"drapeaux consultation : publié={res.published} "
                     f"attributaire={res.winner_declared}")
    # trois lignes, deux lots : rien ne doit être fusionné, chaque lot a sa
    # propre composition donc sa propre observation
    if len(res.bidders) != 3:
        fails.append(f"{len(res.bidders)} lignes au lieu de 3 — fusionner les lots "
                     "ferait disparaître des observations exploitables")
    lots = res.lots()
    if sorted(lots) != ["1", "2"]:
        fails.append(f"regroupement par lot incorrect : {sorted(lots)}")
    elif [b.rank for b in lots["1"]] != [1, 2]:
        fails.append(f"tri par rang dans le lot : {[b.rank for b in lots['1']]}")
    if res.winner is None or res.winner.company != "SOCIETE ALPHA TEXTILE":
        fails.append(f"attributaire erroné : {res.winner}")
    if not res.detail_available:
        fails.append("detail_available faux alors que les montants sont là")

    # --- lignes sans montant : présentes à l'écran, absentes des observations
    rc_np = ResultsClient(client=StubClient({
        "checkResultat": "Y", "cehckWinner": "N",
        "openProgressSupCls": [{"bizRegNm": "GALAXY", "bizRegNo": "1785794Q",
                                "shopCls": "1", "ineligibleCd": "00", "rk": 1}],
        "spFailResult": []}))
    r_np = rc_np.for_notice("consultation", "1", "00")
    if len(r_np.bidders) != 1:
        fails.append("une ligne sans prix doit rester visible")
    if r_np.lots():
        fails.append("une ligne sans prix ne doit pas produire d'observation")
    if r_np.detail_available:
        fails.append("detail_available vrai alors qu'aucun montant n'est publié")

    # --- appel d'offres : lecture du vrai payload, un lot par ligne
    rc_ao = ResultsClient(client=StubClient({
        "execTypeChkOpen": "Y", "execTypeChkEval": "Y", "check/publication": "Y",
        "ranking/rankLot": [ROW_AO, ROW_AO_2], "listLotInfructueux": []}))
    r_ao = rc_ao.for_notice("ao", "20241002293", "00")
    if len(r_ao.bidders) != 2:
        fails.append(f"A.O. : {len(r_ao.bidders)} soumissionnaires au lieu de 2")
    elif r_ao.bidders[0].price != 103123.02 or r_ao.bidders[0].reg_no != "1333127X":
        fails.append(f"A.O. : montant ou matricule mal lu : {r_ao.bidders[0]}")
    if sorted(r_ao.lots()) != ["1", "2"]:
        fails.append(f"A.O. : lots mal regroupés : {sorted(r_ao.lots())}")
    if r_ao.bidders[1].retained is not False:
        fails.append("A.O. : « Non retenu » non reconnu")
    # la requête doit passer les paramètres dans l'URL — un corps dataSearch
    # renvoie une liste vide, c'est l'erreur qui avait fait conclure à tort que
    # les A.O. ne publiaient pas leurs montants
    urls = [u for u in rc_ao.c.seen if "rankLot" in u]
    if not urls or "bidNo=20241002293" not in urls[0]:
        fails.append(f"A.O. : paramètres absents de l'URL : {urls}")

    # --- rien de publié : on n'appelle pas le détail pour rien
    rc2 = ResultsClient(client=StubClient({"checkResultat": "N", "cehckWinner": "N"}))
    r2 = rc2.for_notice("consultation", "20260812345", "00")
    if r2.published or r2.winner_declared or r2.bidders:
        fails.append("résultats non publiés : la réponse devrait être vide")
    if any("openProgress" in u for u in rc2.c.seen):
        fails.append("le détail est demandé alors que rien n'est publié")

    # --- appel d'offres : trois drapeaux distincts
    rc3 = ResultsClient(client=StubClient({
        "execTypeChkOpen": "Y", "execTypeChkEval": "N", "check/publication": "N"}))
    r3 = rc3.for_notice("ao", "20260899999", "01")
    if not (r3.opening_published and not r3.eval_published and not r3.winner_declared):
        fails.append(f"drapeaux A.O. : {r3.opening_published}/{r3.eval_published}/"
                     f"{r3.winner_declared}")
    if not r3.published:
        fails.append("ouverture publiée mais published=False")

    # --- une panne réseau ne doit jamais lever
    class Boom(StubClient):
        def get(self, url, timeout=None):  # noqa: ARG002
            raise RuntimeError("réseau coupé")

    r4 = ResultsClient(client=Boom({})).for_notice("consultation", "1", "00")
    if r4.published or r4.bidders:
        fails.append("une panne réseau ne doit pas produire de faux résultat")

    # --- persistance
    with tempfile.TemporaryDirectory() as td:
        st = Store(Path(td) / "t.db")
        if st.get_result("jamais-vu") is not None:
            fails.append("get_result() d'un uid inconnu devrait rendre None")
        st.save_result("u1", res.as_dict())
        got = st.get_result("u1")
        if not got or len(got.get("bidders", [])) != 3:
            fails.append(f"aller-retour SQLite incomplet : {got}")
        if not got.get("checked_at"):
            fails.append("checked_at absent de la relecture")
        # ré-écriture : mise à jour, pas doublon ni erreur de clé
        st.save_result("u1", {"published": False, "winner_declared": False,
                              "bidders": []})
        flags = st.result_flags()
        if flags.get("u1", {}).get("published") is not False:
            fails.append(f"la ré-écriture n'a pas écrasé l'état : {flags}")
        if len(flags) != 1:
            fails.append(f"{len(flags)} lignes de résultat au lieu d'une")
        st.close()

    fails += _http_roundtrip()
    fails += _browser()

    print(f"résultats TUNEPS : {'OK' if not fails else str(len(fails)) + ' échec(s)'}")
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


# ---------------------------------------------------------------------------
def _http_roundtrip() -> list[str]:
    """POST /api/result : appel réel au serveur, portail simulé."""
    from tuneps import webapp
    from tests.test_web import Cfg, _free_port, _req

    fails: list[str] = []
    calls: list[tuple] = []

    class FakeRC:
        def __init__(self, **kw):
            pass

        def for_notice(self, source, number, mod_seq="00", uid=""):
            calls.append((source, number, mod_seq, uid))
            return Result(uid=uid, published=True, winner_declared=True,
                          detail_available=True,
                          bidders=[Bidder(company="ALPHA", price=1.0, rank=1,
                                          retained=True, status_label="Retenu")])

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "t.db"
        st = Store(db)
        n = _from_shop(SHOP_ROWS[0])
        st.record(n, "high", 3.0, ["drapeau"])
        st.set_status(n.uid, "done")
        st.close()

        webapp.Handler.cfg = Cfg(db)
        port = _free_port()
        httpd = ThreadingHTTPServer(("127.0.0.1", port), webapp.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        base = f"http://127.0.0.1:{port}"
        real = results_mod.ResultsClient
        results_mod.ResultsClient = FakeRC
        try:
            # avant toute vérification, la liste ne porte aucun drapeau
            code, d = _req(base + "/api/notices")
            row = next((x for x in d["notices"] if x["uid"] == n.uid), None)
            if row is None:
                fails.append("avis absent de /api/notices")
            elif row.get("result") is not None:
                fails.append(f"drapeau de résultat présent avant vérification : {row['result']}")

            code, r = _req(base + "/api/result", "POST", {"uid": n.uid})
            if code != 200:
                fails.append(f"/api/result code {code} : {r}")
            else:
                if not (r["published"] and r["winner_declared"]):
                    fails.append(f"drapeaux non transmis : {r}")
                if len(r.get("bidders", [])) != 1:
                    fails.append(f"soumissionnaires non transmis : {r}")
                if not r.get("checked_at"):
                    fails.append("checked_at absent de la réponse HTTP")
                if r.get("cached") is not False:
                    fails.append("première vérification annoncée comme mise en cache")
            if len(calls) != 1:
                fails.append(f"{len(calls)} appels au portail au lieu d'un")
            elif calls[0][:2] != (n.source, n.number):
                fails.append(f"mauvais avis interrogé : {calls[0]}")

            # deuxième appel : servi depuis la base, sans retoucher au portail
            code, r2 = _req(base + "/api/result", "POST", {"uid": n.uid})
            if code != 200 or r2.get("cached") is not True:
                fails.append(f"le cache n'a pas servi : code {code}, {r2}")
            if len(calls) != 1:
                fails.append("le cache n'a pas évité un second appel au portail")

            # force : on retourne voir le portail
            code, _ = _req(base + "/api/result", "POST", {"uid": n.uid, "force": True})
            if len(calls) != 2:
                fails.append("« force » n'a pas relancé l'interrogation")

            # la liste porte maintenant le drapeau
            code, d = _req(base + "/api/notices")
            row = next(x for x in d["notices"] if x["uid"] == n.uid)
            if not (row.get("result") or {}).get("winner_declared"):
                fails.append(f"drapeau absent de /api/notices après vérification : {row.get('result')}")

            code, _ = _req(base + "/api/result", "POST", {"uid": "inconnu"})
            if code != 404:
                fails.append(f"uid inconnu accepté (code {code})")

            code, _ = _req(base + "/api/result", "POST", {"uid": n.uid}, headers={})
            if code != 403:
                fails.append(f"requête sans en-tête acceptée (code {code})")
        finally:
            results_mod.ResultsClient = real
            httpd.shutdown()
            httpd.server_close()
    return fails


# ---------------------------------------------------------------------------
def _browser() -> list[str]:
    """La page réelle, dans un vrai navigateur : boutons, panneau, « tout vérifier ».

    Sans Playwright (Raspberry Pi, VPS), on saute — comme pour les comptes à
    rebours : l'installation ne doit pas échouer pour autant.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        print("résultats : moitié navigateur ignorée (playwright absent)")
        return []

    from tuneps import webapp
    from tests.test_web import Cfg, _free_port

    fails: list[str] = []
    muet: list[str] = []          # les avis dont le portail ne publie rien

    class FakeRC:
        def __init__(self, **kw):
            pass

        def for_notice(self, source, number, mod_seq="00", uid=""):
            if uid in muet:
                return Result(uid=uid)
            return Result(uid=uid, published=True, winner_declared=True,
                          detail_available=True,
                          bidders=[Bidder(company="SOCIETE ALPHA TEXTILE", price=12500.5,
                                          rank=1, retained=True, status_label="Retenu"),
                                   Bidder(company="BETA CONFECTION", price=11800.0,
                                          rank=2, retained=False,
                                          status_label="Non retenu",
                                          reason="Dossier incomplet")])

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "t.db"
        st = Store(db)
        ns = [_from_shop(r) for r in SHOP_ROWS][:2]
        for n in ns:
            st.record(n, "high", 3.0, ["drapeau"])
            st.set_status(n.uid, "done")
        # délai passé : c'est la condition pour que TUNEPS publie quoi que ce soit
        st.db.execute("UPDATE notices SET deadline_at = '2026-01-20 09:00:00'")
        st.db.commit()
        st.close()
        muet.append(ns[1].uid)

        webapp.Handler.cfg = Cfg(db)
        port = _free_port()
        httpd = ThreadingHTTPServer(("127.0.0.1", port), webapp.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        real = results_mod.ResultsClient
        results_mod.ResultsClient = FakeRC
        try:
            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page()
                errs: list[str] = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.goto(f"http://127.0.0.1:{port}/")
                # les deux avis sont déjà « soumis » : l'onglet par défaut est
                # vide, on attend donc le compteur plutôt qu'une ligne.
                pg.wait_for_function("() => document.querySelector('#n-done')"
                                     ".textContent === '2'", timeout=10000)

                # la colonne n'existe que dans « Soumis »
                if pg.inner_text("#th-res").strip():
                    fails.append("colonne « Résultat » affichée hors de l'onglet Soumis")
                pg.click('.tab[data-status="done"]')
                pg.wait_for_timeout(150)
                if not pg.inner_text("#th-res").strip():
                    fails.append("colonne « Résultat » absente de l'onglet Soumis")
                if pg.is_hidden("#checkall"):
                    fails.append("« Vérifier tous les résultats » caché alors que "
                                 "deux dossiers sont à vérifier")

                # vérification d'un seul avis : le panneau s'ouvre tout seul
                pg.click(f'button[data-res="{ns[0].uid}"]')
                pg.wait_for_selector("table.bidders", timeout=5000)
                if len(pg.query_selector_all("table.bidders tbody tr")) != 2:
                    fails.append("le panneau n'affiche pas les deux soumissionnaires")
                if len(pg.query_selector_all("table.bidders tr.win")) != 1:
                    fails.append("l'attributaire n'est pas mis en évidence")
                # toLocaleString sépare les milliers par une espace insécable
                montants = re.sub(r"\s+", " ", pg.inner_text("table.bidders"))
                if "12 500,500 TND" not in montants:
                    fails.append(f"montant mal formaté : {montants[:120]}")

                # le bouton referme puis rouvre
                pg.click(f'button[data-open="{ns[0].uid}"]')
                pg.wait_for_timeout(150)
                if pg.query_selector_all("tr.res-row"):
                    fails.append("le panneau ne se referme pas")
                pg.click(f'button[data-open="{ns[0].uid}"]')
                pg.wait_for_timeout(200)
                if not pg.query_selector_all("tr.res-row"):
                    fails.append("le panneau ne se rouvre pas")

                # le second avis : rien de publié, message explicite
                pg.click(f'button[data-res="{ns[1].uid}"]')
                pg.wait_for_timeout(400)
                if not pg.query_selector(".res-none"):
                    fails.append("« pas encore publié » n'est pas affiché")

                # vérification groupée
                # vérification groupée : elle ne doit reprendre que le dossier
                # encore sans résultat, pas celui déjà publié.
                pg.click("#checkall")
                pg.wait_for_timeout(1500)
                bilan = pg.inner_text("#toast")
                if "1 dossier(s) vérifié(s)" not in bilan:
                    fails.append(f"la vérification groupée n'a pas ciblé le seul "
                                 f"dossier restant : « {bilan} »")

                if errs:
                    fails.append("erreurs JavaScript : " + " | ".join(errs))
                b.close()
        finally:
            results_mod.ResultsClient = real
            httpd.shutdown()
            httpd.server_close()
    return fails


if __name__ == "__main__":
    raise SystemExit(run())
