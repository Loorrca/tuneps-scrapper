"""
Bout-en-bout de l'interface locale : on démarre le vrai serveur, on l'interroge
en HTTP, et on vérifie que les changements d'onglet sont bien persistés en base.

Aucun accès réseau externe : le serveur écoute sur la boucle locale et le
rafraîchissement TUNEPS n'est pas sollicité.
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuneps import webapp  # noqa: E402
from tuneps.client import _from_bid, _from_shop  # noqa: E402
from tuneps.store import Store  # noqa: E402
from tests.test_pipeline import BID_ROWS, SHOP_ROWS  # noqa: E402


class Cfg:
    """Configuration minimale — le serveur n'a besoin que de ces champs."""
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.request_timeout = 10
        self.verify_ssl = True
        self.months_back = 1
        self.fuzzy = True
        self.fuzzy_threshold = 0.86
        self.extra_keywords: list = []
        self.extra_exclusions: list = []
        self.web_host = "127.0.0.1"
        self.web_port = 0
        self.web_password = ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_DEFAULT_HEADERS = {"X-Veille": "1"}


def _req(url: str, method: str = "GET", body: dict | None = None,
         headers: "dict | None" = _DEFAULT_HEADERS):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    # attention : un dict vide est volontaire (requête sans en-tête), il ne
    # doit pas retomber sur les en-têtes par défaut.
    for k, v in (headers if headers is not None else _DEFAULT_HEADERS).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, (json.loads(raw) if "json" in ctype else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def run() -> int:
    fails: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "t.db"

        # --- base peuplée comme après une analyse réelle
        st = Store(db)
        notices = [_from_bid(r) for r in BID_ROWS] + [_from_shop(r) for r in SHOP_ROWS]
        keep = [n for n in notices if "drapeau" in (n.title_fr or "").lower()]
        for n in keep:
            st.record(n, "high", 3.0, ["drapeau"])
        # un avis sans échéance, pour vérifier qu'il est relégué en fin de tri
        no_dl = keep[0]
        st.db.execute("UPDATE notices SET deadline_at='' WHERE uid=?", (no_dl.uid,))
        st.db.commit()
        st.start_run("test")
        st.close()

        cfg = Cfg(db)
        webapp.Handler.cfg = cfg
        port = _free_port()
        httpd = ThreadingHTTPServer(("127.0.0.1", port), webapp.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        base = f"http://127.0.0.1:{port}"

        try:
            # --- la page se sert
            code, html = _req(base + "/")
            if code != 200 or "Veille TUNEPS" not in html:
                fails.append(f"page non servie (code {code})")
            for needed in ('data-status="pending"', 'data-status="done"',
                           'data-status="deleted"', "/api/status"):
                if needed not in html:
                    fails.append(f"élément absent de la page : {needed}")

            # --- liste
            code, d = _req(base + "/api/notices")
            if code != 200:
                fails.append(f"/api/notices code {code}")
            elif len(d["notices"]) != len(keep):
                fails.append(f"{len(d['notices'])} avis renvoyés au lieu de {len(keep)}")
            elif d["counts"]["pending"] != len(keep):
                fails.append(f"compteurs initiaux erronés : {d['counts']}")

            # tri : échéance croissante, sans échéance en dernier
            dls = [n["deadline_at"] for n in d["notices"]]
            if dls != sorted(dls, key=lambda x: (x == "", x)):
                fails.append(f"tri par échéance incorrect : {dls}")

            uid = d["notices"][0]["uid"]

            # --- « Soumis » puis « Écarter » puis retour
            for target in ("done", "deleted", "pending"):
                code, r = _req(base + "/api/status", "POST", {"uid": uid, "status": target})
                if code != 200:
                    fails.append(f"passage à {target} refusé (code {code}) : {r}")
                    continue
                st2 = Store(db)
                got = [n["status"] for n in st2.list_notices() if n["uid"] == uid]
                st2.close()
                if got != [target]:
                    fails.append(f"statut non persisté : attendu {target}, en base {got}")
                if r["counts"][target] < 1:
                    fails.append(f"compteurs non mis à jour après {target} : {r['counts']}")

            # --- entrées invalides
            code, _ = _req(base + "/api/status", "POST", {"uid": uid, "status": "bidon"})
            if code != 400:
                fails.append(f"statut invalide accepté (code {code})")
            code, _ = _req(base + "/api/status", "POST", {"uid": "inconnu", "status": "done"})
            if code != 404:
                fails.append(f"uid inconnu accepté (code {code})")

            # --- protection contre un site tiers : sans l'en-tête, refus
            code, _ = _req(base + "/api/status", "POST", {"uid": uid, "status": "done"},
                           headers={})
            if code != 403:
                fails.append(f"requête sans en-tête acceptée (code {code})")
            code, _ = _req(base + "/api/status", "POST", {"uid": uid, "status": "done"},
                           headers={"X-Veille": "1", "Origin": "https://evil.example"})
            if code != 403:
                fails.append(f"origine externe acceptée (code {code})")

            # --- ouverture au réseau local : la page servie sur une IP de LAN
            # doit pouvoir écrire. C'est exactement ce que l'ancienne liste
            # blanche 127.0.0.1/localhost cassait.
            lan = f"192.168.1.42:{port}"
            code, _ = _req(base + "/api/status", "POST", {"uid": uid, "status": "done"},
                           headers={"X-Veille": "1", "Origin": f"http://{lan}",
                                    "Host": lan})
            if code != 200:
                fails.append(f"origine LAN cohérente refusée (code {code}) — "
                             "l'interface serait en lecture seule depuis un autre poste")
            # ... mais une origine qui ne correspond pas au Host reste refusée
            code, _ = _req(base + "/api/status", "POST", {"uid": uid, "status": "done"},
                           headers={"X-Veille": "1", "Origin": "http://192.168.1.99:1",
                                    "Host": lan})
            if code != 403:
                fails.append(f"origine incohérente avec Host acceptée (code {code})")

            code, _ = _req(base + "/api/inexistant")
            if code != 404:
                fails.append(f"route inconnue : code {code}")

            # --- mot de passe partagé (utile dès qu'on ouvre au réseau)
            cfg.web_password = "s3cret-du-bureau"
            code, _ = _req(base + "/api/notices")
            if code != 401:
                fails.append(f"lecture sans mot de passe autorisée (code {code})")
            code, _ = _req(base + "/")
            if code != 401:
                fails.append(f"page servie sans mot de passe (code {code})")

            good = base64.b64encode(b"veille:s3cret-du-bureau").decode()
            bad = base64.b64encode(b"veille:mauvais").decode()
            code, _ = _req(base + "/api/notices",
                           headers={"X-Veille": "1", "Authorization": "Basic " + bad})
            if code != 401:
                fails.append(f"mauvais mot de passe accepté (code {code})")
            code, d2 = _req(base + "/api/notices",
                            headers={"X-Veille": "1", "Authorization": "Basic " + good})
            if code != 200:
                fails.append(f"bon mot de passe refusé (code {code})")
            code, _ = _req(base + "/api/status", "POST", {"uid": uid, "status": "pending"},
                           headers={"X-Veille": "1", "Authorization": "Basic " + good})
            if code != 200:
                fails.append(f"écriture refusée avec le bon mot de passe (code {code})")
            # en-tête malformé : refus, pas de plantage du serveur
            for junk in ("Basic", "Basic !!!!", "Bearer abc", ""):
                code, _ = _req(base + "/api/notices",
                               headers={"X-Veille": "1", "Authorization": junk})
                if code != 401:
                    fails.append(f"en-tête Authorization « {junk} » accepté (code {code})")
            cfg.web_password = ""

            # --- l'énumération d'adresses ne doit jamais lever
            from tuneps.webapp import lan_addresses
            try:
                addrs = lan_addresses()
                if not isinstance(addrs, list):
                    fails.append("lan_addresses() ne renvoie pas une liste")
            except Exception as e:  # noqa: BLE001
                fails.append(f"lan_addresses() a levé : {e}")
        finally:
            httpd.shutdown()
            httpd.server_close()

    print(f"interface web : {'OK' if not fails else str(len(fails)) + ' échec(s)'}")
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
