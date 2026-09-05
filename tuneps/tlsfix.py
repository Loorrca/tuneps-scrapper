"""
Réparation de chaîne TLS incomplète (« unable to get local issuer certificate »).

Le serveur www.tuneps.tn ne renvoie que son certificat final, sans les
certificats intermédiaires qui le relient à une autorité racine connue.
Les navigateurs masquent le problème : quand un maillon manque, Chrome et
Edge vont le télécharger tout seuls à l'adresse inscrite dans l'extension
« Authority Information Access » (AIA) du certificat. Python, curl et
OpenSSL ne le font pas — d'où l'échec.

Ce module fait la même chose que le navigateur : il récupère le certificat
présenté, lit l'URL de l'émetteur, télécharge les maillons manquants, et
construit un magasin de confiance local (certifi + les intermédiaires).
La vérification TLS reste donc ACTIVE — on comble un trou, on ne désactive
pas le contrôle.

Aucune dépendance supplémentaire : l'extension AIA est extraite du DER par
recherche directe de l'OID id-ad-caIssuers (1.3.6.1.5.5.7.48.2).
"""

from __future__ import annotations

import base64
import logging
import socket
import ssl
import textwrap
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

# DER de l'OID 1.3.6.1.5.5.7.48.2 (id-ad-caIssuers) : 06 08 2B 06 01 05 05 07 30 02
_OID_CA_ISSUERS = bytes.fromhex("06082B06010505073002")
# Dans un AccessDescription, l'URI est un GeneralName [6] IMPLICIT IA5String -> tag 0x86
_URI_TAG = 0x86

MAX_DEPTH = 4
TIMEOUT = 20


class TlsRepairError(RuntimeError):
    pass


# ---------------------------------------------------------------- primitives


def fetch_peer_chain_der(host: str, port: int = 443) -> bytes:
    """Certificat final présenté par le serveur, au format DER (sans vérifier)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise TlsRepairError(f"{host} n'a présenté aucun certificat.")
    return der


def extract_ca_issuer_urls(der: bytes) -> list[str]:
    """URLs « CA Issuers » de l'extension AIA, dans l'ordre où elles apparaissent."""
    urls: list[str] = []
    pos = 0
    while True:
        i = der.find(_OID_CA_ISSUERS, pos)
        if i < 0:
            break
        pos = i + len(_OID_CA_ISSUERS)
        # juste après l'OID doit venir le GeneralName [6] : tag 0x86, longueur, valeur
        if pos < len(der) and der[pos] == _URI_TAG:
            length = der[pos + 1]
            if length & 0x80:  # longueur sur plusieurs octets — inhabituel pour une URL
                n = length & 0x7F
                if n > 2:
                    continue
                length = int.from_bytes(der[pos + 2:pos + 2 + n], "big")
                start = pos + 2 + n
            else:
                start = pos + 2
            try:
                url = der[start:start + length].decode("ascii")
            except UnicodeDecodeError:
                continue
            if url.startswith(("http://", "https://")) and url not in urls:
                urls.append(url)
    return urls


def der_to_pem(der: bytes) -> str:
    b64 = base64.b64encode(der).decode("ascii")
    body = "\n".join(textwrap.wrap(b64, 64))
    return f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n"


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tuneps-veille/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310
        return r.read()


def fetch_issuer_der(url: str) -> bytes:
    """Télécharge un certificat émetteur ; accepte DER brut, PEM ou PKCS#7."""
    raw = _download(url)
    if raw.lstrip().startswith(b"-----BEGIN"):
        text = raw.decode("ascii", "ignore")
        blob = text.split("-----BEGIN CERTIFICATE-----")[1].split("-----END CERTIFICATE-----")[0]
        return base64.b64decode("".join(blob.split()))
    if raw[:1] == b"\x30":
        return raw          # DER (SEQUENCE) — cas le plus courant pour un .crt AIA
    raise TlsRepairError(f"Format de certificat non reconnu à {url}")


def _default_ca_pem() -> str:
    """Magasin de confiance système (certifi si présent, sinon celui d'OpenSSL)."""
    try:
        import certifi
        return Path(certifi.where()).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        path = ssl.get_default_verify_paths().cafile
        if path and Path(path).exists():
            return Path(path).read_text(encoding="utf-8")
    raise TlsRepairError("Aucun magasin de certificats racine trouvé sur ce système.")


def _verifies(host: str, port: int, ca_file: Path) -> bool:
    ctx = ssl.create_default_context(cafile=str(ca_file))
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                return True
    except ssl.SSLError:
        return False
    except OSError:
        raise


# ------------------------------------------------------------------ publique


def build_ca_bundle(host: str, out_path: Path, port: int = 443,
                    force: bool = False) -> Path:
    """
    Construit (et met en cache) un magasin de confiance qui complète la chaîne
    de `host`. Retourne le chemin du fichier à passer à `requests(verify=...)`.
    """
    out_path = Path(out_path)
    if out_path.exists() and not force:
        try:
            if _verifies(host, port, out_path):
                return out_path
        except OSError:
            return out_path      # hors ligne : on garde le cache tel quel
        log.info("Le magasin en cache ne convient plus, reconstruction.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pem_parts = [_default_ca_pem()]
    der = fetch_peer_chain_der(host, port)
    seen: set[bytes] = set()

    for depth in range(MAX_DEPTH):
        urls = extract_ca_issuer_urls(der)
        if not urls:
            break
        issuer_der = None
        for url in urls:
            try:
                issuer_der = fetch_issuer_der(url)
                log.info("Maillon intermédiaire récupéré (niveau %d) : %s", depth + 1, url)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("Téléchargement impossible depuis %s : %s", url, e)
        if issuer_der is None or issuer_der in seen:
            break
        seen.add(issuer_der)
        pem_parts.append(der_to_pem(issuer_der))

        candidate = out_path.with_suffix(".tmp")
        candidate.write_text("".join(pem_parts), encoding="utf-8")
        if _verifies(host, port, candidate):
            candidate.replace(out_path)
            log.info("Chaîne TLS complétée : %s", out_path)
            return out_path
        der = issuer_der        # il manque encore un maillon : on remonte

    tmp = out_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    raise TlsRepairError(
        f"Impossible de compléter la chaîne de certificats de {host}. "
        "Le certificat est peut-être émis par une autorité absente de ce système "
        "(TunTrust, par exemple) : dans ce cas installez le certificat racine de "
        "l'autorité, ou mettez « verify_ssl: false » dans config.yaml."
    )


def diagnose(host: str, port: int = 443) -> list[str]:
    """Rapport lisible sur l'état TLS de `host`."""
    out: list[str] = []
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                out.append(f"✓ {host}:{port} — chaîne TLS valide, aucune réparation nécessaire.")
                return out
    except ssl.SSLCertVerificationError as e:
        out.append(f"✗ Vérification du certificat échouée : {e.verify_message or e}")
    except OSError as e:
        out.append(f"✗ Connexion impossible à {host}:{port} : {e}")
        out.append("  (coupure réseau, DNS, pare-feu ou proxy)")
        return out

    try:
        der = fetch_peer_chain_der(host, port)
    except Exception as e:  # noqa: BLE001
        out.append(f"✗ Impossible de lire le certificat présenté : {e}")
        return out

    out.append(f"  Certificat présenté : {len(der)} octets.")
    urls = extract_ca_issuer_urls(der)
    if urls:
        out.append("  Le serveur n'envoie pas la chaîne complète, mais indique où la trouver :")
        for u in urls:
            out.append(f"    → {u}")
        out.append("  C'est réparable automatiquement (c'est ce que fait le navigateur).")
    else:
        out.append("  Aucune adresse « CA Issuers » dans le certificat : réparation "
                   "automatique impossible.")
        out.append("  L'émetteur est probablement une autorité absente de ce système.")
    return out
