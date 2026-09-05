"""
Vérifie la réparation de chaîne TLS sur une panne reproduite localement.

On fabrique une vraie hiérarchie racine -> intermédiaire -> serveur, puis on
monte un serveur HTTPS qui ne présente QUE son certificat final — exactement
le défaut de configuration de www.tuneps.tn. L'intermédiaire manquant est
publié sur un petit serveur HTTP, à l'adresse inscrite dans l'extension AIA
du certificat final.

Le test échoue d'abord (comme en production), puis doit réussir après
réparation automatique.

Nécessite la commande `openssl`. Sans elle, le test se déclare ignoré.
"""

from __future__ import annotations

import http.server
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuneps import tlsfix  # noqa: E402

HOST = "localhost"


def _run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_pki(d: Path, aia_url: str) -> None:
    # --- racine
    _run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(d / "root.key"), "-out", str(d / "root.crt"),
         "-days", "2", "-subj", "/CN=Racine de test")

    # --- intermédiaire (signé par la racine)
    (d / "inter.cnf").write_text("basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=keyCertSign,cRLSign\n")
    _run("openssl", "req", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(d / "inter.key"), "-out", str(d / "inter.csr"),
         "-subj", "/CN=Intermediaire de test")
    _run("openssl", "x509", "-req", "-in", str(d / "inter.csr"),
         "-CA", str(d / "root.crt"), "-CAkey", str(d / "root.key"), "-CAcreateserial",
         "-out", str(d / "inter.crt"), "-days", "2",
         "-extfile", str(d / "inter.cnf"))
    _run("openssl", "x509", "-in", str(d / "inter.crt"), "-outform", "DER",
         "-out", str(d / "inter.der"))

    # --- serveur (signé par l'intermédiaire), avec AIA vers l'intermédiaire
    (d / "leaf.cnf").write_text(
        f"subjectAltName=DNS:{HOST},IP:127.0.0.1\n"
        f"authorityInfoAccess=caIssuers;URI:{aia_url}\n"
        "basicConstraints=CA:FALSE\n"
    )
    _run("openssl", "req", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(d / "leaf.key"), "-out", str(d / "leaf.csr"),
         "-subj", f"/CN={HOST}")
    _run("openssl", "x509", "-req", "-in", str(d / "leaf.csr"),
         "-CA", str(d / "inter.crt"), "-CAkey", str(d / "inter.key"), "-CAcreateserial",
         "-out", str(d / "leaf.crt"), "-days", "2",
         "-extfile", str(d / "leaf.cnf"))


class _CertHandler(http.server.BaseHTTPRequestHandler):
    directory: Path

    def do_GET(self):  # noqa: N802
        body = (self.directory / "inter.der").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pkix-cert")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


def _serve_https(d: Path, port: int) -> threading.Thread:
    """Serveur TLS qui ne présente QUE le certificat final (chaîne tronquée)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(d / "leaf.crt"), keyfile=str(d / "leaf.key"))
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(8)

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            try:
                with ctx.wrap_socket(c, server_side=True) as t:
                    t.recv(1024)
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    return th


def run() -> int:
    if not shutil.which("openssl"):
        print("TLS : ignoré (openssl absent)")
        return 0

    fails: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        http_port, https_port = _free_port(), _free_port()
        aia_url = f"http://127.0.0.1:{http_port}/inter.crt"
        _build_pki(d, aia_url)

        _CertHandler.directory = d
        httpd = http.server.HTTPServer(("127.0.0.1", http_port), _CertHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        _serve_https(d, https_port)

        root_pem = (d / "root.crt").read_text()

        # 1. la racine seule ne suffit pas : c'est la panne de production
        only_root = d / "only_root.pem"
        only_root.write_text(root_pem)
        if tlsfix._verifies(HOST, https_port, only_root):
            fails.append("le serveur de test aurait dû échouer à la vérification "
                         "(chaîne non tronquée ?)")

        # 2. l'AIA du certificat présenté est bien lue
        der = tlsfix.fetch_peer_chain_der(HOST, https_port)
        urls = tlsfix.extract_ca_issuer_urls(der)
        if urls != [aia_url]:
            fails.append(f"AIA mal extraite : {urls}")

        # 3. réparation automatique
        orig = tlsfix._default_ca_pem
        tlsfix._default_ca_pem = lambda: root_pem
        try:
            bundle = tlsfix.build_ca_bundle(HOST, d / "bundle.pem", port=https_port)
            if not tlsfix._verifies(HOST, https_port, bundle):
                fails.append("le magasin reconstruit ne valide toujours pas la chaîne")
            if "BEGIN CERTIFICATE" not in bundle.read_text():
                fails.append("magasin reconstruit vide")
            # 4. le cache est réutilisé sans retélécharger
            httpd.shutdown()                       # plus d'AIA disponible
            again = tlsfix.build_ca_bundle(HOST, d / "bundle.pem", port=https_port)
            if again != bundle:
                fails.append("le magasin en cache n'a pas été réutilisé")
        except tlsfix.TlsRepairError as e:
            fails.append(f"réparation échouée : {e}")
        finally:
            tlsfix._default_ca_pem = orig

    print(f"TLS : {'OK — chaîne tronquée détectée puis réparée' if not fails else str(len(fails)) + ' échec(s)'}")
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
