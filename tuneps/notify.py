"""Envoi de l'alerte par e-mail (SMTP)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate

log = logging.getLogger(__name__)


class MailError(RuntimeError):
    pass


def send_email(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    sender: str,
    sender_name: str,
    recipients: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    use_tls: bool = True,
    timeout: int = 30,
) -> None:
    if not recipients:
        raise MailError("Aucun destinataire configuré (MAIL_TO).")
    if not password:
        raise MailError("Mot de passe SMTP absent (SMTP_PASSWORD dans .env).")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) as s:
                s.login(username, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as s:
                s.ehlo()
                if use_tls:
                    s.starttls(context=ctx)
                    s.ehlo()
                s.login(username, password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(
            "Authentification SMTP refusée. Avec Gmail il faut un « mot de passe "
            "d'application » (16 caractères), pas le mot de passe du compte, et la "
            "validation en deux étapes doit être activée."
        ) from e
    except Exception as e:  # noqa: BLE001
        raise MailError(f"Échec de l'envoi SMTP : {e}") from e

    log.info("E-mail envoyé à %s", ", ".join(recipients))


def desktop_notify(title: str, body: str) -> None:
    """Notification de bureau (best effort, ignore silencieusement l'absence de notify-send)."""
    import shutil
    import subprocess

    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "-u", "normal", title, body], timeout=10, check=False)
    except Exception:  # noqa: BLE001
        pass
