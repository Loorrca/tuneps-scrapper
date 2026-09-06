"""
Interface web locale : tableau des avis avec suivi « en cours / soumis / écarté ».

Serveur HTTP de la bibliothèque standard — aucune dépendance supplémentaire,
pas de framework. Par défaut il n'écoute que sur la boucle locale (127.0.0.1) ;
`--host 0.0.0.0`, ou `web.host` dans config.yaml, l'ouvre au réseau local.
Dans ce cas un mot de passe partagé (WEB_PASSWORD) est vivement conseillé.

Points d'entrée :
  GET  /                  la page
  GET  /api/notices       tous les avis + compteurs
  POST /api/status        {"uid": …, "status": "pending|done|deleted"}
  POST /api/refresh       relance une analyse TUNEPS et renvoie le bilan
  POST /api/result        {"uid": …} interroge TUNEPS pour le résultat d'un avis
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("webui.html")
MAX_BODY = 64 * 1024


def _row_to_dict(r) -> dict:
    try:
        terms = json.loads(r["terms"] or "[]")
    except Exception:  # noqa: BLE001
        terms = []
    return {
        "uid": r["uid"], "source": r["source"], "number": r["number"],
        "title_fr": r["title_fr"] or "", "title_ar": r["title_ar"] or "",
        "title_en": r["title_en"] or "", "buyer": r["buyer"] or "",
        "published_at": r["published_at"] or "", "deadline_at": r["deadline_at"] or "",
        "url": r["url"] or "", "confidence": r["confidence"] or "review",
        "terms": terms, "status": r["status"] or "pending",
        "mod_seq": r["mod_seq"] or "00",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "VeilleTUNEPS"
    cfg = None                 # injectés par serve()
    scan_lock = threading.Lock()

    # ------------------------------------------------------------- outils
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _store(self):
        from .store import Store
        return Store(self.cfg.db_path)

    def _guard(self) -> bool:
        """
        Empêche un site web ouvert dans le même navigateur d'appeler cette API
        à votre insu.

        Deux verrous :
          - un en-tête maison que seule notre page envoie. Une page tierce ne
            peut pas le poser sans déclencher une requête préalable CORS, à
            laquelle ce serveur ne répond pas : le navigateur bloque.
          - si le navigateur annonce une Origin, elle doit correspondre à
            l'adresse par laquelle la page a été servie.

        La comparaison est faite avec l'en-tête Host, et non avec une liste
        d'adresses locales : sinon, dès qu'on ouvre le service au réseau, la
        page chargée depuis 192.168.x.y verrait tous ses envois refusés.
        """
        if self.headers.get("X-Veille") != "1":
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True                     # requête hors navigateur (curl, tests)
        o = urlparse(origin)
        host_hdr = (self.headers.get("Host") or "").strip()
        served = f"{o.hostname}:{o.port}" if o.port else (o.hostname or "")
        # Host peut arriver avec ou sans port explicite
        return host_hdr in (served, o.hostname or "")

    # ------------------------------------------------------------------
    def _authorized(self) -> bool:
        """
        Mot de passe partagé, facultatif (WEB_PASSWORD dans .env).
        Vide = pas d'authentification, comportement d'origine.
        """
        pw = getattr(self.cfg, "web_password", "") or ""
        if not pw:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[6:], validate=True).decode("utf-8")
        except Exception:  # noqa: BLE001
            return False
        _user, _, given = raw.partition(":")
        # comparaison à temps constant : ne renseigne pas sur le préfixe correct
        return hmac.compare_digest(given, pw)

    def _ask_password(self) -> None:
        body = b"Authentification requise."
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Veille TUNEPS", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("corps de requête trop volumineux")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw or b"{}")

    def log_message(self, fmt, *args):     # journal HTTP dans le fichier, pas la console
        log.debug("%s %s", self.address_string(), fmt % args)

    # -------------------------------------------------------------- routes
    def do_GET(self):  # noqa: N802
        if not self._authorized():
            return self._ask_password()
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/notices":
            st = self._store()
            try:
                runs = st.last_runs(1)
                flags = st.result_flags()
                notices = []
                for r in st.list_notices():
                    d = _row_to_dict(r)
                    d["result"] = flags.get(d["uid"])   # None = jamais vérifié
                    notices.append(d)
                return self._json(200, {
                    "notices": notices,
                    "counts": st.status_counts(),
                    "last_run": (runs[0]["started_at"] or "").replace("T", " ")[:16] if runs else None,
                })
            finally:
                st.close()
        return self._json(404, {"error": "introuvable"})

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            return self._ask_password()
        path = urlparse(self.path).path
        if not self._guard():
            return self._json(403, {"error": "requête refusée"})
        try:
            body = self._body()
        except Exception as e:  # noqa: BLE001
            return self._json(400, {"error": f"corps illisible : {e}"})

        if path == "/api/status":
            uid, status = body.get("uid"), body.get("status")
            st = self._store()
            try:
                if status not in st.STATUSES:
                    return self._json(400, {"error": f"statut inconnu : {status}"})
                if not st.set_status(str(uid), status):
                    return self._json(404, {"error": "avis inconnu"})
                log.info("Suivi : %s -> %s", uid, status)
                return self._json(200, {"ok": True, "counts": st.status_counts()})
            finally:
                st.close()

        if path == "/api/result":
            uid = str(body.get("uid") or "")
            force = bool(body.get("force"))
            st = self._store()
            try:
                row = next((r for r in st.list_notices() if r["uid"] == uid), None)
                if row is None:
                    return self._json(404, {"error": "avis inconnu"})
                cached = None if force else st.get_result(uid)
                if cached is not None:
                    cached["cached"] = True
                    return self._json(200, cached)
                from .results import ResultsClient
                rc = ResultsClient(timeout=self.cfg.request_timeout,
                                   verify_ssl=self.cfg.verify_ssl,
                                   ca_cache=self.cfg.db_path.parent / "tuneps-ca.pem")
                res = rc.for_notice(row["source"], row["number"],
                                    row["mod_seq"] or "00", uid).as_dict()
                st.save_result(uid, res)
                # relecture : c'est elle qui porte checked_at, affiché dans la page
                res = st.get_result(uid) or res
                log.info("Résultat %s : publié=%s attributaire=%s (%d soumissionnaire(s))",
                         row["number"], res["published"], res["winner_declared"],
                         len(res["bidders"]))
                res["cached"] = False
                return self._json(200, res)
            except Exception as e:  # noqa: BLE001
                log.exception("Consultation du résultat")
                return self._json(500, {"error": str(e)})
            finally:
                st.close()

        if path == "/api/refresh":
            if not self.scan_lock.acquire(blocking=False):
                return self._json(409, {"error": "une analyse est déjà en cours"})
            try:
                return self._json(200, self._scan())
            except Exception as e:  # noqa: BLE001
                log.exception("Analyse depuis l'interface web")
                return self._json(500, {"error": str(e)})
            finally:
                self.scan_lock.release()

        return self._json(404, {"error": "introuvable"})

    # ------------------------------------------------------------- analyse
    def _scan(self) -> dict:
        from .client import TunepsClient
        from .matcher import default_matcher

        cfg = self.cfg
        client = TunepsClient(timeout=cfg.request_timeout, verify_ssl=cfg.verify_ssl,
                              ca_cache=cfg.db_path.parent / "tuneps-ca.pem")
        matcher = default_matcher({
            "fuzzy": cfg.fuzzy, "fuzzy_threshold": cfg.fuzzy_threshold,
            "extra_keywords": cfg.extra_keywords, "extra_exclusions": cfg.extra_exclusions,
        })
        st = self._store()
        run_id = st.start_run("web")
        try:
            notices = client.recent(months_back=cfg.months_back)
            matched = new_hits = 0
            for n in notices:
                res = matcher.match(*n.titles)
                if not res.matched:
                    continue
                matched += 1
                if st.record(n, res.confidence, res.score, res.terms):
                    new_hits += 1
            st.end_run(run_id, len(notices), matched, new_hits, ok=True, detail="interface web")
            log.info("Analyse web : %d avis, %d correspondances, %d nouveautés",
                     len(notices), matched, new_hits)
            return {"scanned": len(notices), "matched": matched, "new_hits": new_hits}
        except Exception as e:
            st.end_run(run_id, 0, 0, 0, ok=False, detail=str(e))
            raise
        finally:
            st.close()


def lan_addresses() -> list[str]:
    """Adresses IPv4 de cette machine sur le réseau local, la principale d'abord."""
    found: list[str] = []
    # L'adresse par laquelle on sortirait vers Internet : c'est celle du bon
    # réseau quand la machine a plusieurs interfaces. Aucun paquet n'est émis,
    # connect() sur UDP ne fait que choisir une route.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))          # TEST-NET-1, jamais routé
        found.append(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def serve(cfg, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    Handler.cfg = cfg
    if not PAGE.exists():
        raise FileNotFoundError(f"Page introuvable : {PAGE}")

    httpd = ThreadingHTTPServer((host, port), Handler)
    bound_port = httpd.server_address[1]
    exposed = host not in ("127.0.0.1", "localhost", "::1")
    local_url = f"http://127.0.0.1:{bound_port}/"

    if exposed:
        print(f"Interface : {local_url}   (cette machine)")
        for ip in lan_addresses():
            print(f"            http://{ip}:{bound_port}/   (depuis les autres postes)")
        if getattr(cfg, "web_password", ""):
            print("Mot de passe demandé à l'ouverture (WEB_PASSWORD).")
        else:
            print()
            print("  /!\  Ouvert à tout le réseau local, SANS mot de passe :")
            print("       n'importe quel appareil connecté au même Wi-Fi peut lire")
            print("       la liste et cocher « soumis » ou « écarté ».")
            print("       Pour exiger un mot de passe : WEB_PASSWORD=... dans .env")
        print()
        print("  Ne redirigez jamais ce port depuis votre box : le service parle")
        print("  en HTTP, sans chiffrement, et n'est pas fait pour Internet.")
    else:
        print(f"Interface locale : {local_url}")
    print("Ctrl-C pour arrêter.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(local_url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        httpd.server_close()
