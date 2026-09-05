"""Rendu HTML : e-mail (compatible clients de messagerie) et rapport local."""

from __future__ import annotations

import datetime as dt
import html
import json
from typing import Any, Sequence

SOURCE_LABEL = {"ao": "Appel d'offres", "consultation": "Consultation"}
CONF_LABEL = {"high": "Correspondance forte", "review": "À vérifier"}


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _terms(row: Any) -> str:
    raw = row["terms"] if not isinstance(row, dict) else row.get("terms")
    try:
        return ", ".join(json.loads(raw or "[]"))
    except Exception:  # noqa: BLE001
        return _e(raw)


def _days_left(deadline: str) -> int | None:
    """
    Jours de calendrier restants. -1 dès que le délai est écoulé, y compris
    le jour même : une échéance à 09h00 est close à 15h00, elle ne doit pas
    s'afficher comme encore ouverte dans l'e-mail du soir.
    """
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(deadline[:len("2026-09-07 09:00:00")].strip(), fmt)
            break
        except (ValueError, TypeError):
            continue
    else:
        return None
    now = dt.datetime.now()
    if d <= now:
        return -1
    return (d.date() - now.date()).days


def _card(row: Any) -> str:
    title = row["title_fr"] or row["title_ar"] or row["title_en"] or "(sans intitulé)"
    alt = ""
    if row["title_ar"] and row["title_ar"] != row["title_fr"]:
        alt = (f'<div dir="rtl" style="font-size:13px;color:#5b5b5b;margin-top:2px">'
               f'{_e(row["title_ar"])}</div>')
    dl = _days_left(row["deadline_at"] or "")
    if dl is None:
        dl_html = _e(row["deadline_at"] or "—")
    elif dl < 0:
        dl_html = f'<span style="color:#a11">{_e(row["deadline_at"])} (clôturé)</span>'
    elif dl <= 5:
        dl_html = f'<strong style="color:#a11">{_e(row["deadline_at"])} — J-{dl}</strong>'
    else:
        dl_html = f'{_e(row["deadline_at"])} <span style="color:#6b7280">— J-{dl}</span>'

    badge_bg = "#0f766e" if row["confidence"] == "high" else "#b45309"
    return f"""
<tr><td style="padding:0 0 14px 0">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="border:1px solid #e3e5e8;border-radius:10px;background:#fff">
    <tr><td style="padding:14px 16px">
      <div style="font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:#6b7280">
        <span style="display:inline-block;padding:2px 7px;border-radius:4px;background:{badge_bg};
                     color:#fff;font-weight:600;letter-spacing:.02em">
          {_e(CONF_LABEL.get(row['confidence'], row['confidence']))}
        </span>
        &nbsp;{_e(SOURCE_LABEL.get(row['source'], row['source']))}
        &nbsp;·&nbsp;n° {_e(row['number'])}
      </div>
      <div style="font-size:16px;font-weight:600;color:#111827;margin:8px 0 0;line-height:1.35">
        {_e(title)}
      </div>
      {alt}
      <div style="font-size:13px;color:#374151;margin-top:8px">
        <strong>Acheteur :</strong> {_e(row['buyer'] or '—')}
      </div>
      <div style="font-size:13px;color:#374151;margin-top:3px">
        <strong>Publié :</strong> {_e(row['published_at'] or '—')}
        &nbsp;&nbsp;<strong>Limite de dépôt :</strong> {dl_html}
      </div>
      <div style="font-size:12px;color:#6b7280;margin-top:6px">
        Mots-clés détectés : {_e(_terms(row))}
      </div>
      <div style="margin-top:12px">
        <a href="{_e(row['url'])}"
           style="display:inline-block;background:#1d4ed8;color:#fff;text-decoration:none;
                  font-size:13px;font-weight:600;padding:8px 14px;border-radius:6px">
          Ouvrir sur TUNEPS →
        </a>
      </div>
    </td></tr>
  </table>
</td></tr>"""


def render_html(rows: Sequence[Any], title: str = "Nouveaux avis TUNEPS",
                subtitle: str = "") -> str:
    high = [r for r in rows if r["confidence"] == "high"]
    review = [r for r in rows if r["confidence"] != "high"]
    generated = dt.datetime.now().strftime("%d/%m/%Y à %H:%M")

    def section(label: str, items: Sequence[Any]) -> str:
        if not items:
            return ""
        cards = "".join(_card(r) for r in items)
        return f"""
<tr><td style="padding:6px 0 10px">
  <div style="font-size:13px;font-weight:700;color:#111827;letter-spacing:.02em;
              text-transform:uppercase">{_e(label)} ({len(items)})</div>
</td></tr>
{cards}"""

    body = section("Correspondances fortes", high) + section("À vérifier", review)
    if not rows:
        body = ("""<tr><td style="padding:24px;text-align:center;color:#6b7280;font-size:14px">
                Aucun nouvel avis correspondant sur cette période.</td></tr>""")

    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title></head>
<body style="margin:0;padding:0;background:#f3f4f6;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6">
<tr><td align="center" style="padding:24px 12px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px">
    <tr><td style="padding:0 0 18px">
      <div style="font-size:20px;font-weight:700;color:#111827">{_e(title)}</div>
      <div style="font-size:13px;color:#6b7280;margin-top:4px">
        {_e(subtitle) if subtitle else ''}{' · ' if subtitle else ''}Généré le {generated}
      </div>
    </td></tr>
    {body}
    <tr><td style="padding:18px 4px 0;font-size:11px;color:#9ca3af;line-height:1.5">
      Veille automatique du portail des marchés publics tuneps.tn.
      Les avis sont détectés sur leur intitulé ; vérifiez toujours le cahier des
      charges sur TUNEPS avant de vous engager.
    </td></tr>
  </table>
</td></tr></table></body></html>"""


def render_text(rows: Sequence[Any]) -> str:
    if not rows:
        return "Aucun nouvel avis correspondant.\n"
    out = []
    for r in rows:
        out.append(
            f"[{CONF_LABEL.get(r['confidence'], r['confidence'])}] "
            f"{SOURCE_LABEL.get(r['source'], r['source'])} n°{r['number']}\n"
            f"  {r['title_fr'] or r['title_ar'] or r['title_en']}\n"
            f"  Acheteur : {r['buyer']}\n"
            f"  Publié : {r['published_at']} | Limite : {r['deadline_at']}\n"
            f"  Mots-clés : {_terms(r)}\n"
            f"  {r['url']}\n")
    return "\n".join(out)
