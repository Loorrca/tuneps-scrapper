"""État persistant : SQLite. Sert à ne notifier chaque avis qu'une seule fois."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .client import Notice

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    uid           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    number        TEXT NOT NULL,
    mod_seq       TEXT NOT NULL,
    master_id     INTEGER,
    title_fr      TEXT,
    title_ar      TEXT,
    title_en      TEXT,
    buyer         TEXT,
    published_at  TEXT,
    deadline_at   TEXT,
    url           TEXT,
    confidence    TEXT,
    score         REAL,
    terms         TEXT,
    first_seen    TEXT NOT NULL,
    notified_at   TEXT,
    -- suivi manuel depuis l'interface web
    status        TEXT NOT NULL DEFAULT 'pending',   -- pending | done | deleted
    status_at     TEXT,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_notices_notified ON notices(notified_at);
CREATE INDEX IF NOT EXISTS idx_notices_published ON notices(published_at);
-- l'index sur status est créé dans _migrate(), après l'ajout éventuel de la
-- colonne : sur une base antérieure à l'interface web, elle n'existe pas encore.

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    ended_at   TEXT,
    mode       TEXT,
    scanned    INTEGER,
    matched    INTEGER,
    new_hits   INTEGER,
    ok         INTEGER,
    detail     TEXT
);
"""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        """Ajoute les colonnes de suivi aux bases créées avant l'interface web."""
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(notices)")}
        for name, ddl in (
            ("status", "ALTER TABLE notices ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"),
            ("status_at", "ALTER TABLE notices ADD COLUMN status_at TEXT"),
            ("note", "ALTER TABLE notices ADD COLUMN note TEXT"),
        ):
            if name not in cols:
                self.db.execute(ddl)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_notices_status ON notices(status)")

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------
    def is_known(self, uid: str) -> bool:
        cur = self.db.execute("SELECT 1 FROM notices WHERE uid = ?", (uid,))
        return cur.fetchone() is not None

    def record(self, notice: Notice, confidence: str, score: float, terms: list[str]) -> bool:
        """Insère l'avis s'il est inconnu. Retourne True s'il s'agit d'une nouveauté."""
        if self.is_known(notice.uid):
            return False
        self.db.execute(
            """INSERT INTO notices
               (uid, source, number, mod_seq, master_id, title_fr, title_ar, title_en,
                buyer, published_at, deadline_at, url, confidence, score, terms, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (notice.uid, notice.source, notice.number, notice.mod_seq, notice.master_id,
             notice.title_fr, notice.title_ar, notice.title_en, notice.buyer,
             notice.published_at, notice.deadline_at, notice.url,
             confidence, score, json.dumps(terms, ensure_ascii=False), _now()),
        )
        self.db.commit()
        return True

    def mark_notified(self, uids: Iterable[str]) -> None:
        ts = _now()
        self.db.executemany("UPDATE notices SET notified_at = ? WHERE uid = ?",
                            [(ts, u) for u in uids])
        self.db.commit()

    def pending(self) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT * FROM notices WHERE notified_at IS NULL ORDER BY published_at DESC"))

    def recent_matches(self, limit: int = 200) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT * FROM notices ORDER BY published_at DESC LIMIT ?", (limit,)))

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"]

    # ------------------------------------------------------------------
    # Suivi manuel (interface web)
    # ------------------------------------------------------------------
    STATUSES = ("pending", "done", "deleted")

    def list_notices(self, status: str | None = None) -> list[sqlite3.Row]:
        """Avis triés par échéance croissante ; ceux sans échéance en dernier."""
        sql = ("SELECT * FROM notices "
               + ("WHERE status = ? " if status else "")
               + "ORDER BY CASE WHEN deadline_at IS NULL OR deadline_at = '' THEN 1 ELSE 0 END, "
                 "deadline_at ASC, published_at DESC")
        return list(self.db.execute(sql, (status,) if status else ()))

    def set_status(self, uid: str, status: str, note: str | None = None) -> bool:
        if status not in self.STATUSES:
            raise ValueError(f"statut inconnu : {status}")
        cur = self.db.execute(
            "UPDATE notices SET status = ?, status_at = ?, note = COALESCE(?, note) "
            "WHERE uid = ?", (status, _now(), note, uid))
        self.db.commit()
        return cur.rowcount > 0

    def status_counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status, COUNT(*) c FROM notices GROUP BY status")
        counts = {s: 0 for s in self.STATUSES}
        for r in rows:
            counts[r["status"]] = r["c"]
        return counts

    # ------------------------------------------------------------------
    def start_run(self, mode: str) -> int:
        cur = self.db.execute(
            "INSERT INTO runs (started_at, mode) VALUES (?, ?)", (_now(), mode))
        self.db.commit()
        return int(cur.lastrowid)

    def end_run(self, run_id: int, scanned: int, matched: int, new_hits: int,
                ok: bool, detail: str = "") -> None:
        self.db.execute(
            """UPDATE runs SET ended_at=?, scanned=?, matched=?, new_hits=?, ok=?, detail=?
               WHERE id=?""",
            (_now(), scanned, matched, new_hits, 1 if ok else 0, detail[:2000], run_id))
        self.db.commit()

    def last_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)))
