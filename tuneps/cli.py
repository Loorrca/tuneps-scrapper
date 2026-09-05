"""Point d'entrée en ligne de commande."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config as config_mod
from . import keywords as K
from .client import Notice, TunepsClient
from .matcher import default_matcher
from .notify import MailError, desktop_notify, send_email
from .report import render_html, render_text
from .store import Store

log = logging.getLogger("tuneps")


def setup_logging(path: Path, verbose: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    # Rotation : sur une machine allumée en permanence (Raspberry Pi, VPS) un
    # journal sans limite finit par remplir la carte SD, surtout si une panne
    # réseau fait boucler les messages d'erreur. 1 Mo x 3 = 3 Mo au maximum.
    file_handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                       encoding="utf-8")
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=fmt,
        handlers=[file_handler, logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------


def _scan(cfg, mode: str, months_back: int, stems: list[str] | None) -> tuple[list[Notice], int]:
    client = TunepsClient(timeout=cfg.request_timeout, verify_ssl=cfg.verify_ssl,
                          ca_cache=cfg.db_path.parent / 'tuneps-ca.pem')
    if mode == "keywords":
        notices = client.keyword_search(stems or K.SERVER_STEMS)
    else:
        notices = client.recent(months_back=months_back)
    return notices, len(notices)


def cmd_run(args) -> int:
    cfg = config_mod.load(args.config)
    setup_logging(cfg.log_path, args.verbose)
    store = Store(cfg.db_path)
    matcher = default_matcher({
        "fuzzy": cfg.fuzzy,
        "fuzzy_threshold": cfg.fuzzy_threshold,
        "extra_keywords": cfg.extra_keywords,
        "extra_exclusions": cfg.extra_exclusions,
    })

    mode = args.mode
    months_back = args.months_back if args.months_back is not None else cfg.months_back
    run_id = store.start_run(mode)
    log.info("Analyse TUNEPS — mode=%s, fenêtre=%s mois", mode, months_back + 1)

    try:
        notices, scanned = _scan(cfg, mode, months_back, args.keyword)
    except Exception as e:  # noqa: BLE001
        log.exception("Échec de la récupération des avis")
        store.end_run(run_id, 0, 0, 0, ok=False, detail=str(e))
        return 2

    log.info("%d avis récupérés", scanned)

    matched = new_hits = 0
    for n in notices:
        res = matcher.match(n.title_fr, n.title_ar, n.title_en)
        if not res.matched:
            continue
        matched += 1
        if store.record(n, res.confidence, res.score, res.terms):
            new_hits += 1
            log.info("NOUVEAU [%s] %s — %s (%s)",
                     res.confidence, n.number, (n.title_fr or n.title_ar)[:80], res.summary())

    log.info("%d avis correspondants, dont %d nouveaux", matched, new_hits)

    rows = store.pending()
    if args.resend_all:
        rows = store.recent_matches(limit=args.resend_all)

    subtitle = f"{scanned} avis analysés · fenêtre {months_back + 1} mois"
    html_doc = render_html(rows, subtitle=subtitle)
    text_doc = render_text(rows)

    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    report_path = cfg.report_dir / f"tuneps_{stamp}.html"
    report_path.write_text(html_doc, encoding="utf-8")
    latest = cfg.report_dir / "dernier_rapport.html"
    latest.write_text(html_doc, encoding="utf-8")
    log.info("Rapport écrit : %s", report_path)

    sent = False
    if rows or cfg.mail.send_when_empty:
        if cfg.mail.enabled and not args.no_email:
            n_high = sum(1 for r in rows if r["confidence"] == "high")
            subject = (f"TUNEPS — {len(rows)} avis à voir"
                       + (f" ({n_high} forte(s))" if n_high else ""))
            try:
                send_email(
                    host=cfg.mail.host, port=cfg.mail.port, username=cfg.mail.username,
                    password=cfg.mail.password, sender=cfg.mail.sender,
                    sender_name=cfg.mail.sender_name, recipients=cfg.mail.recipients,
                    subject=subject, html_body=html_doc, text_body=text_doc,
                    use_tls=cfg.mail.use_tls, timeout=cfg.request_timeout,
                )
                sent = True
            except MailError as e:
                log.error("E-mail non envoyé : %s", e)
        if cfg.desktop_notification and rows:
            desktop_notify("Veille TUNEPS",
                           f"{len(rows)} avis correspondant à vos mots-clés.")

    if sent and not args.resend_all:
        store.mark_notified([r["uid"] for r in rows])

    store.end_run(run_id, scanned, matched, new_hits, ok=True,
                  detail=f"rapport={report_path.name} email={'oui' if sent else 'non'}")
    print(f"\n{scanned} avis analysés · {matched} correspondances · {new_hits} nouveautés")
    print(f"Rapport : {latest}")
    if rows and not sent:
        print("E-mail non envoyé (désactivé, non configuré, ou erreur — voir le journal).")
    store.close()
    return 0


def cmd_test_email(args) -> int:
    cfg = config_mod.load(args.config)
    setup_logging(cfg.log_path, args.verbose)

    class Fake(dict):
        def __getitem__(self, k):  # noqa: D105
            return self.get(k)

    demo = [Fake(
        source="consultation", number="S20260900999", confidence="high",
        title_fr="Acquisition des drapeaux, des guirlandes et des accessoires de décoration",
        title_ar="اقتناء أعلام وزينة لتزيين الشوارع", title_en="",
        buyer="Commune de Tunis", published_at="2026-09-03 10:00:00",
        deadline_at="2026-09-20 09:00:00", terms='["drapeau", "guirlande", "زينة"]',
        url="https://www.tuneps.tn/portail/consultations",
    )]
    html_doc = render_html(demo, title="Test — Veille TUNEPS",
                           subtitle="Message de vérification de la configuration")
    try:
        send_email(
            host=cfg.mail.host, port=cfg.mail.port, username=cfg.mail.username,
            password=cfg.mail.password, sender=cfg.mail.sender,
            sender_name=cfg.mail.sender_name, recipients=cfg.mail.recipients,
            subject="TUNEPS — test de configuration", html_body=html_doc,
            text_body=render_text(demo), use_tls=cfg.mail.use_tls,
        )
    except MailError as e:
        print(f"ÉCHEC : {e}")
        return 1
    print(f"E-mail de test envoyé à : {', '.join(cfg.mail.recipients)}")
    return 0


def cmd_doctor(args) -> int:
    """Diagnostic réseau / TLS, et réparation de la chaîne de certificats."""
    from . import tlsfix
    from .client import BID_URL, TunepsClient

    cfg = config_mod.load(args.config)
    setup_logging(cfg.log_path, args.verbose)
    host = "www.tuneps.tn"
    print(f"Diagnostic de {host}\n")
    for line in tlsfix.diagnose(host):
        print(line)

    if not cfg.verify_ssl:
        print("\n⚠ verify_ssl est désactivé dans config.yaml : les certificats ne "
              "sont pas contrôlés.")

    cache = cfg.db_path.parent / "tuneps-ca.pem"
    print("\nTentative de réparation automatique de la chaîne…")
    try:
        bundle = tlsfix.build_ca_bundle(host, cache, force=args.force)
        print(f"✓ Chaîne complétée. Magasin local : {bundle}")
    except tlsfix.TlsRepairError as e:
        print(f"✗ {e}")
        return 1
    except OSError as e:
        print(f"✗ Réseau indisponible : {e}")
        return 1

    print("\nTest d'un appel réel à l'API…")
    client = TunepsClient(timeout=cfg.request_timeout, verify_ssl=cfg.verify_ssl, ca_cache=cache)
    rows = client._post(BID_URL, [{"key": "bidNo", "value": "202601%", "specificSearch": "like"}])
    if rows:
        print(f"✓ L'API répond : {len(rows)} avis reçus pour janvier 2026.")
        print("  Tout est en ordre — relancez  ./veille run")
        return 0
    print("✗ L'API n'a rien renvoyé. Voir le journal : " + str(cfg.log_path))
    return 1


def cmd_web(args) -> int:
    """Interface locale de suivi des avis (tableau + onglets)."""
    from . import webapp

    cfg = config_mod.load(args.config)
    setup_logging(cfg.log_path, args.verbose)
    Store(cfg.db_path).close()          # crée/migre la base avant de servir
    host = args.host or cfg.web_host
    port = args.port or cfg.web_port
    webapp.serve(cfg, host=host, port=port, open_browser=not args.no_browser)
    return 0


def cmd_status(args) -> int:
    cfg = config_mod.load(args.config)
    store = Store(cfg.db_path)
    c = store.status_counts()
    print(f"Base      : {cfg.db_path}")
    print(f"Avis retenus en base : {store.count()}")
    print(f"En attente d'envoi   : {len(store.pending())}")
    print(f"Suivi     : {c['pending']} en cours · {c['done']} soumis · {c['deleted']} écartés")
    print("\nDernières exécutions :")
    for r in store.last_runs(8):
        print(f"  #{r['id']:<4} {r['started_at']}  mode={r['mode']:<9} "
              f"analysés={r['scanned'] or 0:<6} match={r['matched'] or 0:<4} "
              f"nouveaux={r['new_hits'] or 0:<4} {'ok' if r['ok'] else 'ÉCHEC'}")
    store.close()
    return 0


def cmd_check(args) -> int:
    """Teste le dictionnaire sur un intitulé donné (mise au point des mots-clés)."""
    cfg = config_mod.load(args.config)
    matcher = default_matcher({
        "fuzzy": cfg.fuzzy, "fuzzy_threshold": cfg.fuzzy_threshold,
        "extra_keywords": cfg.extra_keywords, "extra_exclusions": cfg.extra_exclusions,
    })
    res = matcher.match(args.text)
    print(f"texte      : {args.text}")
    print(f"match      : {res.matched}  ({res.confidence}, score={res.score:.1f})")
    if res.excluded_by:
        print(f"exclu par  : {res.excluded_by}")
    for h in res.hits:
        kind = "exact" if h.exact else f"flou {h.ratio}"
        print(f"  - [{h.tier}] {h.term}  ({kind} → « {h.found} »)")
    return 0


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tuneps",
        description="Veille automatique des marchés publics TUNEPS "
                    "(drapeaux, banderoles, oriflammes, guirlandes, impression textile).")
    p.add_argument("--config", help="chemin de config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="analyser le portail et notifier les nouveautés")
    r.add_argument("--mode", choices=["sweep", "keywords"], default="sweep",
                   help="sweep = fenêtre mensuelle + appariement flou local (défaut) ; "
                        "keywords = recherche serveur sur tout l'historique")
    r.add_argument("--months-back", type=int, default=None,
                   help="nombre de mois précédents à inclure (défaut : config.yaml)")
    r.add_argument("--keyword", action="append",
                   help="mode keywords : restreindre à ce(s) terme(s)")
    r.add_argument("--no-email", action="store_true", help="produire le rapport sans envoyer d'e-mail")
    r.add_argument("--resend-all", type=int, metavar="N",
                   help="réenvoyer les N derniers avis retenus (ne modifie pas l'état)")
    r.set_defaults(func=cmd_run)

    t = sub.add_parser("test-email", help="envoyer un message de test")
    t.set_defaults(func=cmd_test_email)

    d = sub.add_parser("doctor", help="diagnostiquer réseau/TLS et réparer la chaîne de certificats")
    d.add_argument("--force", action="store_true", help="reconstruire le magasin même s'il existe")
    d.set_defaults(func=cmd_doctor)

    w = sub.add_parser("web", help="ouvrir l'interface locale de suivi des avis")
    w.add_argument("--port", type=int, default=None, help="défaut : web.port de config.yaml")
    w.add_argument("--host", default=None,
                   help="127.0.0.1 (défaut) = cette machine seulement ; "
                        "0.0.0.0 = ouvert au réseau local")
    w.add_argument("--lan", dest="host", action="store_const", const="0.0.0.0",
                   help="raccourci pour --host 0.0.0.0")
    w.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    w.set_defaults(func=cmd_web)

    s = sub.add_parser("status", help="état de la base et dernières exécutions")
    s.set_defaults(func=cmd_status)

    c = sub.add_parser("check", help="tester le dictionnaire sur un intitulé")
    c.add_argument("text")
    c.set_defaults(func=cmd_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
