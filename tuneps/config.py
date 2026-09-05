"""Chargement de la configuration : config.yaml + .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class MailConfig:
    enabled: bool = True
    host: str = "smtp.gmail.com"
    port: int = 587
    use_tls: bool = True
    username: str = ""
    password: str = ""
    sender: str = ""
    sender_name: str = "Veille TUNEPS"
    recipients: list[str] = field(default_factory=list)
    send_when_empty: bool = False


@dataclass
class Config:
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    web_password: str = ""
    db_path: Path = ROOT / "data" / "tuneps.db"
    report_dir: Path = ROOT / "reports"
    log_path: Path = ROOT / "data" / "tuneps.log"
    months_back: int = 1
    fuzzy: bool = True
    fuzzy_threshold: float = 0.86
    extra_keywords: list[str] = field(default_factory=list)
    extra_exclusions: list[str] = field(default_factory=list)
    desktop_notification: bool = True
    verify_ssl: bool = True
    request_timeout: int = 60
    mail: MailConfig = field(default_factory=MailConfig)
    raw: dict[str, Any] = field(default_factory=dict)


def _split_env_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]


def load(config_path: str | Path | None = None, env_path: str | Path | None = None) -> Config:
    load_dotenv(env_path or ROOT / ".env")

    path = Path(config_path or ROOT / "config.yaml")
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    m = raw.get("mail", {}) or {}
    mail = MailConfig(
        enabled=bool(m.get("enabled", True)),
        host=os.getenv("SMTP_HOST", m.get("host", "smtp.gmail.com")),
        port=int(os.getenv("SMTP_PORT", m.get("port", 587))),
        use_tls=bool(m.get("use_tls", True)),
        username=os.getenv("SMTP_USERNAME", m.get("username", "")),
        password=os.getenv("SMTP_PASSWORD", ""),
        sender=os.getenv("MAIL_FROM", m.get("sender", "")) or os.getenv("SMTP_USERNAME", ""),
        sender_name=m.get("sender_name", "Veille TUNEPS"),
        recipients=_split_env_list(os.getenv("MAIL_TO")) or list(m.get("recipients", []) or []),
        send_when_empty=bool(m.get("send_when_empty", False)),
    )

    w = raw.get("web", {}) or {}
    cfg = Config(
        web_host=os.getenv("WEB_HOST", w.get("host", "127.0.0.1")),
        web_port=int(os.getenv("WEB_PORT", w.get("port", 8765))),
        web_password=os.getenv("WEB_PASSWORD", w.get("password", "")),
        db_path=Path(raw.get("db_path") or ROOT / "data" / "tuneps.db"),
        report_dir=Path(raw.get("report_dir") or ROOT / "reports"),
        log_path=Path(raw.get("log_path") or ROOT / "data" / "tuneps.log"),
        months_back=int(raw.get("months_back", 1)),
        fuzzy=bool(raw.get("fuzzy", True)),
        fuzzy_threshold=float(raw.get("fuzzy_threshold", 0.86)),
        extra_keywords=list(raw.get("extra_keywords", []) or []),
        extra_exclusions=list(raw.get("extra_exclusions", []) or []),
        desktop_notification=bool(raw.get("desktop_notification", True)),
        verify_ssl=bool(raw.get("verify_ssl", True)),
        request_timeout=int(raw.get("request_timeout", 60)),
        mail=mail,
        raw=raw,
    )
    return cfg
