"""
Prix marché : le solveur, l'identité des concurrents, la persistance, l'API et
l'écran.

Le test central est celui de COUVERTURE : on fabrique des marchés à partir de
prix qu'on connaît, on ajoute la variation de prix qu'un concurrent réel a d'un
acheteur à l'autre, et on vérifie que les fourchettes calculées contiennent
bien les vrais prix. C'est la seule chose qui compte : des intervalles étroits
qui ratent la vérité sont pires qu'inutiles, ils sont trompeurs.

Ce test a d'ailleurs attrapé le défaut d'origine — voir
`couverture_sans_correction` plus bas, qui verrouille la correction.
"""

from __future__ import annotations

import random
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuneps import pricing  # noqa: E402
from tuneps.market import match_name, normalize_name  # noqa: E402
from tuneps.pricing import Observation, predict, solve  # noqa: E402
from tuneps.store import Store  # noqa: E402

ARTS = list(range(1, 17))


def synth(n: int, noise: float, seed: int = 0
          ) -> tuple[dict[int, float], list[Observation]]:
    """Des marchés engendrés par des prix qu'on connaît."""
    rnd = random.Random(seed)
    true = {a: round(rnd.uniform(5, 150), 3) for a in ARTS}
    obs = []
    for i in range(n):
        items = {a: float(rnd.randint(10, 500))
                 for a in rnd.sample(ARTS, rnd.randint(2, 6))}
        amount = sum(q * true[a] for a, q in items.items())
        if noise:
            amount *= 1 + rnd.uniform(-noise, noise)
        obs.append(Observation(i, f"AO-{i}", items, amount))
    return true, obs


def coverage(true: dict[int, float], est) -> tuple[int, int]:
    """Combien de vrais prix tombent dans leur fourchette."""
    ok = seen = 0
    for r in est.ranges:
        if not r.n_obs or r.low is None or r.high is None:
            continue
        seen += 1
        if r.low - 1e-6 <= true[r.article_id] <= r.high + 1e-6:
            ok += 1
    return ok, seen


def run() -> int:
    fails: list[str] = []

    # --- données parfaites : les prix doivent être retrouvés exactement
    true, obs = synth(40, 0.0, seed=1)
    est = solve(obs, ARTS)
    if est.tolerance > 1e-6:
        fails.append(f"données exactes : t* devrait être nul, vaut {est.tolerance}")
    ok, seen = coverage(true, est)
    if ok != seen or seen < 16:
        fails.append(f"données exactes : {ok}/{seen} prix retrouvés (attendu 16/16)")
    worst = max((r.high - r.low for r in est.ranges if r.low is not None), default=0)
    if worst > 1e-3:
        fails.append(f"données exactes : intervalles non ponctuels (largeur max {worst})")

    # --- données bruitées : LA propriété qui compte
    for n, noise in ((25, 0.05), (40, 0.10), (60, 0.20)):
        true, obs = synth(n, noise, seed=n)
        est = solve(obs, ARTS)
        ok, seen = coverage(true, est)
        if ok < seen * 0.9:
            fails.append(f"couverture insuffisante à n={n}, bruit ±{noise:.0%} : "
                         f"{ok}/{seen} — des fourchettes qui ratent les vrais prix "
                         "sont trompeuses")
        # la dispersion retenue doit être du bon ordre, sinon les intervalles
        # sont soit faux (trop bas) soit inexploitables (trop haut)
        # `noise` est déjà l'écart relatif maximal : un tirage uniforme dans
        # [-noise, +noise] s'écarte au plus de noise, et t est défini de la même
        # façon (|Σqp − T| ≤ t·T). Pas de facteur 2 à introduire ici.
        if not (0.7 <= est.dispersion / noise <= 1.6):
            fails.append(f"dispersion mal calibrée à n={n} : retenue "
                         f"{est.dispersion:.3f} pour un bruit réel de {noise:.3f}")

    # --- verrou : sans la correction, tout s'effondre. Si quelqu'un « simplifie »
    # en réutilisant t* directement, ce test doit tomber.
    true, obs = synth(40, 0.10, seed=99)
    ref = solve(obs, ARTS)
    brut = solve(obs, ARTS, dispersion=ref.tolerance)
    ok_b, seen_b = coverage(true, brut)
    if ok_b > seen_b * 0.5:
        fails.append("le test témoin ne discrimine plus : utiliser t* brut devrait "
                     "manquer la plupart des vrais prix")
    ok_c, seen_c = coverage(true, ref)
    if ok_c <= ok_b:
        fails.append(f"la correction n'apporte rien : {ok_c}/{seen_c} corrigé "
                     f"contre {ok_b}/{seen_b} brut")

    # --- articles toujours liés : non séparables, et ça doit se voir
    rnd = random.Random(5)
    tprice = {1: 10.0, 2: 25.0, 3: 7.0}
    lies = []
    for i in range(20):
        q1 = float(rnd.randint(10, 100))
        # l'article 2 toujours dans un rapport fixe avec le 1 : la paire n'est
        # identifiable qu'ensemble
        items = {1: q1, 2: 2 * q1, 3: float(rnd.randint(5, 60))}
        lies.append(Observation(i, "", items,
                                sum(q * tprice[a] for a, q in items.items())))
    e2 = solve(lies, [1, 2, 3])
    r1 = next(r for r in e2.ranges if r.article_id == 1)
    r3 = next(r for r in e2.ranges if r.article_id == 3)
    if r1.width is not None and r1.width < 1.0:
        fails.append("colinéarité : l'article 1 ne devrait pas être identifié "
                     f"seul (largeur {r1.width})")
    if r3.width is None or r3.width > 1e-3:
        fails.append(f"l'article 3, lui, est identifiable : largeur {r3.width}")

    # --- prédiction : la somme optimisée bat la somme des intervalles
    true, obs = synth(40, 0.08, seed=3)
    est = solve(obs, ARTS)
    q = {2: 100.0, 5: 40.0, 9: 250.0}
    p = predict(obs, ARTS, q)
    vrai = sum(v * true[a] for a, v in q.items())
    if p["low"] is None or not (p["low"] <= vrai <= p["high"]):
        fails.append(f"prédiction : le vrai total {vrai:.0f} hors de "
                     f"[{p['low']} ; {p['high']}]")
    naive_lo = sum(v * next(r for r in est.ranges if r.article_id == a).low
                   for a, v in q.items())
    naive_hi = sum(v * next(r for r in est.ranges if r.article_id == a).high
                   for a, v in q.items())
    if (p["high"] - p["low"]) >= (naive_hi - naive_lo):
        fails.append("prédiction : optimiser la somme devrait donner une "
                     "fourchette plus étroite que la somme des fourchettes")

    # --- pas d'observation : pas de calcul, pas de plantage
    vide = solve([], ARTS)
    if vide.feasible or not vide.error:
        fails.append("aucune observation : devrait être infaisable et le dire")

    fails += _names()
    fails += _store()
    fails += _http()
    fails += _browser()

    print(f"prix marché : {'OK' if not fails else str(len(fails)) + ' échec(s)'}")
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


# ---------------------------------------------------------------------------
def _names() -> list[str]:
    fails: list[str] = []
    same = ["STE ALPHA TEXTILE SARL", "Société Alpha-Textile", "ALPHA TEXTILE",
            "  alpha   textile  "]
    norms = {normalize_name(s) for s in same}
    if len(norms) != 1:
        fails.append(f"graphies équivalentes normalisées différemment : {norms}")

    cand = {normalize_name("STE ALPHA TEXTILE SARL"): 1,
            normalize_name("BETA CONFECTION"): 2}
    cid, _, _ = match_name(normalize_name("ALPHA TEXTILES"), cand)
    if cid != 1:
        fails.append("le pluriel « ALPHA TEXTILES » n'est pas rapproché")
    cid, _, _ = match_name(normalize_name("GAMMA PAVOIS"), cand)
    if cid is not None:
        fails.append("une société sans rapport a été rapprochée")
    # le garde-fou du mot commun : sans lui, deux noms courts et proches par les
    # lettres seraient fusionnés à tort
    cid, _, _ = match_name(normalize_name("BETA CONFECTIONS SUD"),
                           {normalize_name("BETA CONFECTION"): 2})
    if cid is not None:
        fails.append("« BETA CONFECTIONS SUD » fusionné avec « BETA CONFECTION » — "
                     "c'est peut-être une autre société")
    if normalize_name("SARL STE") == "":
        fails.append("un nom fait uniquement de formes juridiques ne doit pas "
                     "devenir vide, sinon toutes ces sociétés se confondent")
    return fails


def _store() -> list[str]:
    fails: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        st = Store(Path(td) / "m.db")
        if len(st.articles()) != 16:
            fails.append(f"{len(st.articles())} articles au lieu de 16")

        st.save_market_tender("AO-1", "Sousse", "2026-01-10", {1: 100, 3: 50},
                              [{"name": "STE ALPHA TEXTILE SARL", "amount": 12500},
                               {"name": "BETA CONFECTION", "amount": 11800}])
        r = st.save_market_tender("AO-2", "Sfax", "2026-02-10", {1: 200, 5: 10},
                                  [{"name": "Société Alpha-Textile", "amount": 24000}])
        if r["bids"][0]["competitor_id"] != 1:
            fails.append("une graphie variante a créé un second concurrent")
        comp = {c["id"]: c for c in st.competitors()}
        if comp[1]["n_obs"] != 2:
            fails.append(f"observations mal comptées : {comp[1]['n_obs']}")

        # deux lignes ramenées au même concurrent : refus silencieux interdit
        r = st.save_market_tender("AO-3", "Tunis", "2026-03-10", {1: 10},
                                  [{"name": "ALPHA TEXTILE", "amount": 1000},
                                   {"name": "ALPHA TEXTILES", "amount": 999}])
        if not r["conflicts"]:
            fails.append("deux graphies du même concurrent sur un marché : le "
                         "conflit doit être signalé, pas écrasé en silence")
        amounts = [b["amount"] for t in st.market_tenders() if t["ref"] == "AO-3"
                   for b in t["bids"]]
        if amounts != [1000]:
            fails.append(f"le premier montant devait être conservé : {amounts}")

        obs = st.observations(1)
        if len(obs) != 3 or obs[0]["qty"] != {1: 100.0, 3: 50.0}:
            fails.append(f"équations mal reconstituées : {obs}")

        st.set_us(1, True)
        st.set_our_price(1, 42.5)
        if st.our_prices() != {1: 42.5}:
            fails.append("prix de référence non enregistré")
        st.set_our_price(1, None)
        if st.our_prices():
            fails.append("prix de référence non effaçable")

        # BETA n'a qu'un montant, sur AO-1, où ALPHA en a déjà un : les deux ne
        # peuvent pas coexister une fois fusionnés. Le montant est perdu — ce
        # qui doit être ANNONCÉ, pas subi.
        m = st.merge_competitors(2, 1)
        if not m["ok"]:
            fails.append("fusion refusée")
        if len(st.competitors()) != 1:
            fails.append("la fusion n'a pas supprimé le concurrent absorbé")
        if m["dropped"] != 1:
            fails.append(f"montant écrasé par la fusion non signalé : {m}")
        if st.competitors()[0]["n_obs"] != 3:
            fails.append(f"marchés après fusion : {st.competitors()[0]['n_obs']}")
        if st.merge_competitors(1, 1)["ok"]:
            fails.append("fusionner un concurrent avec lui-même devrait échouer")

        # renommer vers un nom déjà pris est refusé plutôt que de créer un doublon
        st.resolve_competitor("DELTA PAVOIS")
        if st.rename_competitor(1, "DELTA PAVOIS"):
            fails.append("renommage vers un nom déjà pris accepté")
        st.close()
    return fails


def _http() -> list[str]:
    fails: list[str] = []
    from tuneps import webapp
    from tests.test_web import Cfg, _free_port, _req

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "m.db"
        Store(db).close()
        webapp.Handler.cfg = Cfg(db)
        port = _free_port()
        httpd = ThreadingHTTPServer(("127.0.0.1", port), webapp.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        base = f"http://127.0.0.1:{port}"
        try:
            code, d = _req(base + "/api/market")
            if code != 200 or len(d["articles"]) != 16:
                fails.append(f"GET /api/market : code {code}")

            true, obs = synth(30, 0.07, seed=42)
            for o in obs:
                code, _ = _req(base + "/api/market/tender", "POST", {
                    "ref": o.label, "buyer": "Acheteur", "tdate": "2026-01-01",
                    "items": {str(k): v for k, v in o.qty.items()},
                    "bids": [{"name": "ALPHA", "amount": o.amount}]})
                if code != 200:
                    fails.append(f"POST tender : code {code}")
                    break

            code, d = _req(base + "/api/market/estimate", "POST", {})
            if code != 200 or not d["estimates"]:
                fails.append(f"POST estimate : code {code}")
            else:
                e = d["estimates"][0]
                ins = sum(1 for r in e["ranges"] if r["n_obs"] and
                          r["low"] <= true[r["article_id"]] <= r["high"])
                tot = sum(1 for r in e["ranges"] if r["n_obs"])
                if ins < tot * 0.9:
                    fails.append(f"via HTTP : couverture {ins}/{tot}")
                if e["dispersion_source"] != "corrigée":
                    fails.append("la dispersion automatique n'est pas annoncée corrigée")

            code, d = _req(base + "/api/market/estimate", "POST", {"dispersion": 0.3})
            if code != 200 or d["estimates"][0]["dispersion_source"] != "imposée":
                fails.append("dispersion imposée non prise en compte")

            code, d = _req(base + "/api/market/predict", "POST",
                           {"items": {"1": 100, "3": 40}})
            if code != 200 or d["predictions"][0]["low"] is None:
                fails.append(f"POST predict : code {code} {d}")

            code, _ = _req(base + "/api/market/tender", "POST",
                           {"items": {}, "bids": [{"name": "X", "amount": 1}]})
            if code != 400:
                fails.append(f"marché sans quantité accepté (code {code})")
            code, _ = _req(base + "/api/market/tender", "POST",
                           {"items": {"1": 5}, "bids": []})
            if code != 400:
                fails.append(f"marché sans montant accepté (code {code})")
            code, _ = _req(base + "/api/market/inconnue", "POST", {})
            if code != 404:
                fails.append(f"action inconnue : code {code}")

            # la protection contre les sites tiers couvre aussi ces routes
            code, _ = _req(base + "/api/market/tender", "POST",
                           {"items": {"1": 1}, "bids": [{"name": "X", "amount": 1}]},
                           headers={})
            if code != 403:
                fails.append(f"écriture sans en-tête acceptée (code {code})")
        finally:
            httpd.shutdown()
            httpd.server_close()
    return fails


def _browser() -> list[str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        print("prix marché : moitié navigateur ignorée (playwright absent)")
        return []

    from tuneps import webapp
    from tests.test_web import Cfg, _free_port

    fails: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "m.db"
        st = Store(db)
        st.set_article(1, "Drapeau national 1m", "pièce")
        true, obs = synth(24, 0.06, seed=8)
        for o in obs:
            st.save_market_tender(o.label, "Commune", "2026-01-01", o.qty,
                                  [{"name": "ALPHA TEXTILE", "amount": o.amount},
                                   {"name": "NOUS", "amount": o.amount * 1.05}])
        nous = next(c for c in st.competitors() if c["name"] == "NOUS")
        st.set_us(nous["id"], True)
        for a in (1, 2, 3):
            st.set_our_price(a, true[a] * 1.05)
        st.close()

        webapp.Handler.cfg = Cfg(db)
        port = _free_port()
        httpd = ThreadingHTTPServer(("127.0.0.1", port), webapp.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        try:
            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page(viewport={"width": 1400, "height": 950})
                errs: list[str] = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.goto(f"http://127.0.0.1:{port}/")
                pg.wait_for_timeout(300)

                # la veille reste intacte tant qu'on n'a pas basculé
                if pg.is_hidden("#view-veille") or not pg.is_hidden("#view-market"):
                    fails.append("la bascule de vue part du mauvais côté")
                pg.click('.nav button[data-view="market"]')
                pg.wait_for_selector("#mv-tenders table.grid", timeout=10000)
                if not pg.is_hidden("#view-veille"):
                    fails.append("la veille reste affichée sous Prix marché")
                if len(pg.query_selector_all("#mv-tenders tbody tr")) != 24:
                    fails.append("les marchés ne sont pas tous listés")

                # saisie d'un marché depuis l'écran
                pg.click("#newT")
                pg.wait_for_selector('[data-qgrid="form"]')
                pg.fill("#f-ref", "AO-TEST")
                pg.fill('[data-qgrid="form"] input[data-art="1"]', "10")
                pg.fill("#bids .bidrow .bn", "ALPHA TEXTILE")
                pg.fill("#bids .bidrow .ba", "500")
                pg.click("#saveT")
                pg.wait_for_function(
                    "() => document.querySelectorAll('#mv-tenders tbody tr').length === 25",
                    timeout=10000)

                # concurrents : les deux sociétés, dont la nôtre
                pg.click('.mtab[data-mv="competitors"]')
                pg.wait_for_timeout(200)
                if len(pg.query_selector_all("#mv-competitors tbody tr")) != 2:
                    fails.append("liste des concurrents incorrecte")
                if not pg.query_selector("input[data-us]:checked"):
                    fails.append("« c'est nous » non reporté depuis la base")

                # estimations : le calcul tourne et la vérification s'affiche
                pg.click('.mtab[data-mv="estimates"]')
                pg.wait_for_timeout(150)
                pg.click("#runEst")
                pg.wait_for_selector("#mv-estimates table.grid", timeout=60000)
                pg.wait_for_timeout(300)
                txt = pg.inner_text("#mv-estimates")
                if "Vérification réussie" not in txt:
                    fails.append("la vérification sur nos propres prix n'aboutit "
                                 f"pas : {txt[:200]}")
                if "dispersion retenue" not in txt:
                    fails.append("la dispersion retenue n'est pas affichée")

                # simulateur
                pg.click('.mtab[data-mv="simulator"]')
                pg.wait_for_timeout(150)
                pg.fill('[data-qgrid="sim"] input[data-art="1"]', "100")
                pg.click("#runSim")
                pg.wait_for_selector("#mv-simulator table.grid", timeout=60000)
                if len(pg.query_selector_all("#mv-simulator tbody tr")) != 2:
                    fails.append("le simulateur n'estime pas les deux concurrents")

                # retour à la veille : rien de cassé
                pg.click('.nav button[data-view="veille"]')
                pg.wait_for_timeout(150)
                pg.click('.tab[data-status="done"]')
                pg.wait_for_timeout(150)
                if pg.is_hidden("#view-veille"):
                    fails.append("retour à la veille impossible")

                if errs:
                    fails.append("erreurs JavaScript : " + " | ".join(errs))
                b.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
    return fails


if __name__ == "__main__":
    raise SystemExit(run())
