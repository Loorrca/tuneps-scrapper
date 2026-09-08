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

CREATE TABLE IF NOT EXISTS results (
    uid            TEXT PRIMARY KEY,
    checked_at     TEXT NOT NULL,
    published      INTEGER NOT NULL DEFAULT 0,
    winner_declared INTEGER NOT NULL DEFAULT 0,
    detail         TEXT
);

-- ---------------------------------------------------------------------------
-- Module « Prix marché » : reconstitution des prix unitaires des concurrents
-- à partir des montants globaux qu'ils déposent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS articles (
    id     INTEGER PRIMARY KEY,          -- 1..16
    label  TEXT NOT NULL,
    unit   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS competitors (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,               -- graphie affichée
    norm    TEXT NOT NULL UNIQUE,        -- forme normalisée, clé de rapprochement
    reg_no  TEXT NOT NULL DEFAULT '',    -- matricule fiscal : identité sûre quand TUNEPS le donne
    is_us   INTEGER NOT NULL DEFAULT 0,  -- notre société : sert au test de précision
    created_at TEXT
);
-- PAS d'index sur reg_no ici : sur une base créée par la version précédente la
-- colonne n'existe pas encore, et CREATE INDEX échouerait avant que _migrate()
-- ait pu l'ajouter. Même piège que l'index sur notices(status). Voir _migrate().

-- toutes les graphies rencontrées, y compris celle du nom canonique
CREATE TABLE IF NOT EXISTS competitor_aliases (
    norm          TEXT PRIMARY KEY,
    competitor_id INTEGER NOT NULL,
    raw           TEXT,
    seen_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_alias_comp ON competitor_aliases(competitor_id);

CREATE TABLE IF NOT EXISTS market_tenders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    uid        TEXT,                     -- notices.uid si importé depuis la veille
    lot        TEXT NOT NULL DEFAULT '', -- un marché à plusieurs lots donne une entrée par lot
    ref        TEXT NOT NULL DEFAULT '',
    buyer      TEXT NOT NULL DEFAULT '',
    tdate      TEXT NOT NULL DEFAULT '', -- date du marché (AAAA-MM-JJ)
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT
);
-- idem pour l'index (uid, lot) : la colonne lot est ajoutée par _migrate().

CREATE TABLE IF NOT EXISTS tender_items (
    tender_id  INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    qty        REAL NOT NULL,
    PRIMARY KEY (tender_id, article_id)
);

CREATE TABLE IF NOT EXISTS bids (
    tender_id     INTEGER NOT NULL,
    competitor_id INTEGER NOT NULL,
    amount        REAL NOT NULL,
    PRIMARY KEY (tender_id, competitor_id)
);
CREATE INDEX IF NOT EXISTS idx_bids_comp ON bids(competitor_id);

-- prix unitaires réels de NOTRE société, saisis à la main : c'est l'étalon
-- qui permet de vérifier que les intervalles calculés encadrent la vérité
CREATE TABLE IF NOT EXISTS our_prices (
    article_id INTEGER PRIMARY KEY,
    price      REAL NOT NULL
);

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


# Catalogue de départ : 16 articles. Le nombre n'est pas figé — on peut en
# ajouter et en retirer depuis l'écran Articles, et le calcul s'y adapte : il
# reçoit la liste des articles, rien n'y est câblé à 16.
N_ARTICLES = 16


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
        # colonnes ajoutées après la première version du module « prix marché »
        for table, name, ddl in (
            ("market_tenders", "lot",
             "ALTER TABLE market_tenders ADD COLUMN lot TEXT NOT NULL DEFAULT ''"),
            ("competitors", "reg_no",
             "ALTER TABLE competitors ADD COLUMN reg_no TEXT NOT NULL DEFAULT ''"),
        ):
            cols = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if name not in cols:
                self.db.execute(ddl)
        # ces index dépendent des colonnes ci-dessus : ils viennent APRÈS
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_comp_reg ON competitors(reg_no)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_mt_uid_lot "
                        "ON market_tenders(uid, lot)")
        # amorçage à la création de la base seulement ; ensuite l'utilisateur ajoute
        if not self.db.execute("SELECT 1 FROM articles LIMIT 1").fetchone():
            self.db.executemany(
                "INSERT INTO articles (id, label, unit) VALUES (?,?,'')",
                [(i, f"Article {i}") for i in range(1, N_ARTICLES + 1)])

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
    # Résultats publiés par TUNEPS
    # ------------------------------------------------------------------
    def save_result(self, uid: str, payload: dict) -> None:
        self.db.execute(
            """INSERT INTO results (uid, checked_at, published, winner_declared, detail)
               VALUES (?,?,?,?,?)
               ON CONFLICT(uid) DO UPDATE SET
                 checked_at = excluded.checked_at,
                 published = excluded.published,
                 winner_declared = excluded.winner_declared,
                 detail = excluded.detail""",
            (uid, _now(), 1 if payload.get("published") else 0,
             1 if payload.get("winner_declared") else 0,
             json.dumps(payload, ensure_ascii=False)))
        self.db.commit()

    def get_result(self, uid: str) -> dict | None:
        row = self.db.execute("SELECT * FROM results WHERE uid = ?", (uid,)).fetchone()
        if row is None:
            return None
        try:
            detail = json.loads(row["detail"] or "{}")
        except Exception:  # noqa: BLE001
            detail = {}
        detail["checked_at"] = row["checked_at"]
        return detail

    def result_flags(self) -> dict[str, dict]:
        """État des résultats pour tous les avis, pour l'affichage de la liste."""
        out: dict[str, dict] = {}
        for r in self.db.execute(
                "SELECT uid, checked_at, published, winner_declared FROM results"):
            out[r["uid"]] = {
                "checked_at": r["checked_at"],
                "published": bool(r["published"]),
                "winner_declared": bool(r["winner_declared"]),
            }
        return out

    # ------------------------------------------------------------------
    # Prix marché : articles
    # ------------------------------------------------------------------
    def articles(self) -> list[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM articles ORDER BY id"))

    def set_article(self, article_id: int, label: str, unit: str = "") -> None:
        self.db.execute("UPDATE articles SET label = ?, unit = ? WHERE id = ?",
                        (label.strip() or f"Article {article_id}", unit.strip(),
                         int(article_id)))
        self.db.commit()

    def add_article(self, label: str = "", unit: str = "") -> dict:
        row = self.db.execute("SELECT COALESCE(MAX(id), 0) m FROM articles").fetchone()
        new_id = int(row["m"]) + 1
        self.db.execute("INSERT INTO articles (id, label, unit) VALUES (?,?,?)",
                        (new_id, label.strip() or f"Article {new_id}", unit.strip()))
        self.db.commit()
        return {"id": new_id}

    def delete_article(self, article_id: int) -> dict:
        """Refuse tant que l'article sert dans un marché : le supprimer viderait
        des observations sans prévenir et fausserait tous les calculs."""
        aid = int(article_id)
        used = self.db.execute(
            "SELECT COUNT(*) c FROM tender_items WHERE article_id = ?", (aid,)).fetchone()["c"]
        if used:
            return {"ok": False, "used": used}
        if self.db.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"] <= 1:
            return {"ok": False, "used": 0, "error": "il faut au moins un article"}
        self.db.execute("DELETE FROM articles WHERE id = ?", (aid,))
        self.db.execute("DELETE FROM our_prices WHERE article_id = ?", (aid,))
        self.db.commit()
        return {"ok": True, "used": 0}

    def our_prices(self) -> dict[int, float]:
        return {r["article_id"]: r["price"]
                for r in self.db.execute("SELECT * FROM our_prices")}

    def set_our_price(self, article_id: int, price: float | None) -> None:
        if price is None or price <= 0:
            self.db.execute("DELETE FROM our_prices WHERE article_id = ?",
                            (int(article_id),))
        else:
            self.db.execute(
                "INSERT INTO our_prices (article_id, price) VALUES (?,?) "
                "ON CONFLICT(article_id) DO UPDATE SET price = excluded.price",
                (int(article_id), float(price)))
        self.db.commit()

    # ------------------------------------------------------------------
    # Prix marché : concurrents
    # ------------------------------------------------------------------
    def competitors(self) -> list[dict]:
        rows = self.db.execute("""
            SELECT c.*,
                   (SELECT COUNT(*) FROM bids b WHERE b.competitor_id = c.id) AS n_obs,
                   (SELECT COUNT(*) FROM competitor_aliases a
                     WHERE a.competitor_id = c.id) AS n_alias
              FROM competitors c ORDER BY n_obs DESC, c.name""")
        out = []
        for r in rows:
            d = dict(r)
            d["is_us"] = bool(d["is_us"])
            d["aliases"] = [a["raw"] or a["norm"] for a in self.db.execute(
                "SELECT raw, norm FROM competitor_aliases WHERE competitor_id = ? "
                "ORDER BY norm", (r["id"],))]
            out.append(d)
        return out

    def _alias_index(self) -> dict[str, int]:
        """Toutes les formes connues -> concurrent, noms canoniques inclus."""
        idx = {r["norm"]: r["id"] for r in
               self.db.execute("SELECT id, norm FROM competitors")}
        for r in self.db.execute("SELECT norm, competitor_id FROM competitor_aliases"):
            idx.setdefault(r["norm"], r["competitor_id"])
        return idx

    def resolve_competitor(self, raw: str, create: bool = True,
                           reg_no: str = "") -> dict:
        """Rattache une raison sociale à un concurrent, ou en crée un.

        Le matricule fiscal, quand TUNEPS le fournit, prime sur le nom : c'est
        une identité exacte, là où le rapprochement de graphies est une
        heuristique qui se trompe en silence.

        Retourne {id, name, created, score, matched} — `score` et `matched`
        documentent un rapprochement approché, pour que l'interface puisse le
        montrer plutôt que de le faire en douce.
        """
        from .market import normalize_name, match_name
        raw = (raw or "").strip()
        reg_no = (reg_no or "").strip()
        norm = normalize_name(raw)

        if reg_no:
            hit = self.db.execute("SELECT id, name FROM competitors WHERE reg_no = ?",
                                  (reg_no,)).fetchone()
            if hit:
                if norm:            # mémoriser la graphie rencontrée
                    self.db.execute(
                        "INSERT OR IGNORE INTO competitor_aliases (norm, competitor_id, "
                        "raw, seen_at) VALUES (?,?,?,?)", (norm, hit["id"], raw, _now()))
                    self.db.commit()
                return {"id": hit["id"], "name": hit["name"], "created": False,
                        "score": 1.0, "matched": reg_no, "by": "matricule"}

        if not norm:
            return {"id": None, "name": "", "created": False, "score": 0.0,
                    "matched": "", "error": "nom vide"}

        cid, score, matched = match_name(norm, self._alias_index())
        if cid is not None:
            if score < 1.0:            # graphie nouvelle : on la mémorise
                self.db.execute(
                    "INSERT OR IGNORE INTO competitor_aliases (norm, competitor_id, "
                    "raw, seen_at) VALUES (?,?,?,?)", (norm, cid, raw, _now()))
                self.db.commit()
            row = self.db.execute("SELECT name, reg_no FROM competitors WHERE id = ?",
                                  (cid,)).fetchone()
            # première fois qu'on connaît son matricule : on le fixe, les
            # rapprochements suivants seront exacts au lieu d'être approchés
            if reg_no and row is not None and not row["reg_no"]:
                self.db.execute("UPDATE competitors SET reg_no = ? WHERE id = ?",
                                (reg_no, cid))
                self.db.commit()
            return {"id": cid, "name": row["name"] if row else raw,
                    "created": False, "score": score, "matched": matched, "by": "nom"}

        if not create:
            return {"id": None, "name": raw, "created": False, "score": 0.0,
                    "matched": ""}
        cur = self.db.execute(
            "INSERT INTO competitors (name, norm, reg_no, created_at) VALUES (?,?,?,?)",
            (raw, norm, reg_no, _now()))
        cid = int(cur.lastrowid)
        self.db.execute("INSERT OR IGNORE INTO competitor_aliases "
                        "(norm, competitor_id, raw, seen_at) VALUES (?,?,?,?)",
                        (norm, cid, raw, _now()))
        self.db.commit()
        return {"id": cid, "name": raw, "created": True, "score": 1.0, "matched": "",
                "by": "matricule" if reg_no else "nom"}

    def rename_competitor(self, cid: int, name: str) -> bool:
        from .market import normalize_name
        name = (name or "").strip()
        if not name:
            return False
        norm = normalize_name(name)
        other = self.db.execute(
            "SELECT id FROM competitors WHERE norm = ? AND id <> ?",
            (norm, int(cid))).fetchone()
        if other:                      # le nom voulu est déjà celui d'un autre
            return False
        self.db.execute("UPDATE competitors SET name = ?, norm = ? WHERE id = ?",
                        (name, norm, int(cid)))
        self.db.execute("INSERT OR IGNORE INTO competitor_aliases "
                        "(norm, competitor_id, raw, seen_at) VALUES (?,?,?,?)",
                        (norm, int(cid), name, _now()))
        self.db.commit()
        return True

    def set_us(self, cid: int, is_us: bool) -> None:
        # un seul « c'est nous » : sinon le test de précision n'a plus de sens
        if is_us:
            self.db.execute("UPDATE competitors SET is_us = 0")
        self.db.execute("UPDATE competitors SET is_us = ? WHERE id = ?",
                        (1 if is_us else 0, int(cid)))
        self.db.commit()

    def merge_competitors(self, src: int, dst: int) -> dict:
        """Verse `src` dans `dst`. Les alias suivent.

        Si les deux ont un montant sur le MÊME marché, ils ne peuvent pas
        coexister une fois fusionnés : celui de `dst` l'emporte et celui de
        `src` est perdu. Le nombre de montants ainsi écrasés est renvoyé —
        l'interface doit le dire, une fusion ne s'annule pas.
        """
        src, dst = int(src), int(dst)
        if src == dst:
            return {"ok": False, "dropped": 0, "error": "même concurrent"}
        got = self.db.execute("SELECT id FROM competitors WHERE id IN (?,?)",
                              (src, dst)).fetchall()
        if len(got) != 2:
            return {"ok": False, "dropped": 0, "error": "concurrent inconnu"}
        dropped = self.db.execute(
            "SELECT COUNT(*) c FROM bids s JOIN bids d ON d.tender_id = s.tender_id "
            "AND d.competitor_id = ? WHERE s.competitor_id = ?",
            (dst, src)).fetchone()["c"]
        self.db.execute(
            "INSERT OR IGNORE INTO bids (tender_id, competitor_id, amount) "
            "SELECT tender_id, ?, amount FROM bids WHERE competitor_id = ?",
            (dst, src))
        self.db.execute("DELETE FROM bids WHERE competitor_id = ?", (src,))
        self.db.execute("UPDATE OR IGNORE competitor_aliases SET competitor_id = ? "
                        "WHERE competitor_id = ?", (dst, src))
        self.db.execute("DELETE FROM competitor_aliases WHERE competitor_id = ?",
                        (src,))
        row = self.db.execute("SELECT name, norm FROM competitors WHERE id = ?",
                              (src,)).fetchone()
        if row:
            self.db.execute("INSERT OR IGNORE INTO competitor_aliases "
                            "(norm, competitor_id, raw, seen_at) VALUES (?,?,?,?)",
                            (row["norm"], dst, row["name"], _now()))
        self.db.execute("DELETE FROM competitors WHERE id = ?", (src,))
        self.db.commit()
        return {"ok": True, "dropped": int(dropped), "error": ""}

    # ------------------------------------------------------------------
    # Prix marché : marchés observés
    # ------------------------------------------------------------------
    def save_market_tender(self, ref: str, buyer: str, tdate: str,
                           items: dict[int, float], bids: list[dict],
                           note: str = "", uid: str | None = None,
                           tender_id: int | None = None, lot: str = "") -> dict:
        """Crée ou remplace un marché avec sa composition et ses montants.

        `bids` : [{"name": "…", "amount": 12500.0}] ou {"competitor_id": …}.
        Les noms sont rapprochés au passage ; le détail du rapprochement est
        renvoyé pour que l'interface puisse l'afficher.
        """
        if tender_id:
            self.db.execute(
                "UPDATE market_tenders SET ref=?, buyer=?, tdate=?, note=?, uid=?, lot=? "
                "WHERE id=?", (ref, buyer, tdate, note, uid, lot, int(tender_id)))
            tid = int(tender_id)
            self.db.execute("DELETE FROM tender_items WHERE tender_id = ?", (tid,))
            self.db.execute("DELETE FROM bids WHERE tender_id = ?", (tid,))
        else:
            cur = self.db.execute(
                "INSERT INTO market_tenders (uid, lot, ref, buyer, tdate, note, created_at) "
                "VALUES (?,?,?,?,?,?,?)", (uid, lot, ref, buyer, tdate, note, _now()))
            tid = int(cur.lastrowid)

        self.db.executemany(
            "INSERT INTO tender_items (tender_id, article_id, qty) VALUES (?,?,?)",
            [(tid, int(a), float(q)) for a, q in items.items() if float(q or 0) > 0])

        resolved: list[dict] = []
        conflicts: list[dict] = []
        seen: dict[int, str] = {}          # concurrent -> graphie déjà retenue
        for b in bids:
            amount = float(b.get("amount") or 0)
            if amount <= 0:
                continue
            cid = b.get("competitor_id")
            raw = str(b.get("name") or "")
            info = {"id": cid, "created": False, "score": 1.0, "matched": ""}
            if not cid:
                info = self.resolve_competitor(raw, reg_no=str(b.get("reg_no") or ""))
                cid = info.get("id")
            if not cid:
                continue
            cid = int(cid)
            # Deux lignes du même marché ramenées au même concurrent : c'est
            # soit un rapprochement abusif, soit une double saisie. Dans les
            # deux cas, écraser en silence ferait disparaître un montant — on
            # garde le premier et on remonte le conflit à l'interface.
            if cid in seen:
                conflicts.append({"competitor_id": cid, "kept": seen[cid],
                                  "dropped": raw or f"concurrent {cid}",
                                  "amount": amount,
                                  "matched": info.get("matched", "")})
                continue
            seen[cid] = raw or f"concurrent {cid}"
            self.db.execute(
                "INSERT INTO bids (tender_id, competitor_id, amount) VALUES (?,?,?) "
                "ON CONFLICT(tender_id, competitor_id) DO UPDATE SET "
                "amount = excluded.amount", (tid, cid, amount))
            resolved.append({"competitor_id": cid, "amount": amount,
                             "raw": raw, "created": info.get("created"),
                             "score": info.get("score"), "matched": info.get("matched")})
        self.db.commit()
        return {"tender_id": tid, "bids": resolved, "conflicts": conflicts}

    def market_tenders(self) -> list[dict]:
        out: list[dict] = []
        names = {c["id"]: c["name"] for c in
                 self.db.execute("SELECT id, name FROM competitors")}
        items: dict[int, dict[int, float]] = {}
        for r in self.db.execute("SELECT * FROM tender_items"):
            items.setdefault(r["tender_id"], {})[r["article_id"]] = r["qty"]
        bids: dict[int, list[dict]] = {}
        for r in self.db.execute("SELECT * FROM bids"):
            bids.setdefault(r["tender_id"], []).append({
                "competitor_id": r["competitor_id"],
                "name": names.get(r["competitor_id"], "?"),
                "amount": r["amount"]})
        for r in self.db.execute(
                "SELECT * FROM market_tenders ORDER BY tdate DESC, ref, lot, id DESC"):
            d = dict(r)
            d["items"] = items.get(r["id"], {})
            d["bids"] = sorted(bids.get(r["id"], []), key=lambda b: b["amount"])
            # sans composition, le marché est stocké mais n'entre dans aucun calcul
            d["needs_items"] = not d["items"]
            out.append(d)
        return out

    def delete_market_tender(self, tender_id: int) -> bool:
        tid = int(tender_id)
        self.db.execute("DELETE FROM tender_items WHERE tender_id = ?", (tid,))
        self.db.execute("DELETE FROM bids WHERE tender_id = ?", (tid,))
        cur = self.db.execute("DELETE FROM market_tenders WHERE id = ?", (tid,))
        self.db.commit()
        return cur.rowcount > 0

    def market_keys(self) -> set[tuple[str, str]]:
        """Couples (avis, lot) déjà versés — un marché à lots en produit plusieurs."""
        return {(r["uid"], r["lot"] or "") for r in self.db.execute(
            "SELECT uid, lot FROM market_tenders WHERE uid IS NOT NULL AND uid <> ''")}

    def market_uids(self) -> set[str]:
        """Avis de la veille déjà versés, tous lots confondus."""
        return {u for u, _ in self.market_keys()}

    def observations(self, competitor_id: int) -> list[dict]:
        """Les équations d'un concurrent : composition + montant déposé."""
        rows = self.db.execute("""
            SELECT t.id, t.ref, t.buyer, t.tdate, b.amount
              FROM bids b JOIN market_tenders t ON t.id = b.tender_id
             WHERE b.competitor_id = ? ORDER BY t.tdate, t.id""", (int(competitor_id),))
        items: dict[int, dict[int, float]] = {}
        for r in self.db.execute("SELECT * FROM tender_items"):
            items.setdefault(r["tender_id"], {})[r["article_id"]] = r["qty"]
        out = []
        for r in rows:
            qty = items.get(r["id"], {})
            if not qty:                       # marché sans composition : inutilisable
                continue
            label = r["ref"] or r["buyer"] or f"marché {r['id']}"
            out.append({"tender_id": r["id"], "label": label, "qty": qty,
                        "amount": r["amount"], "tdate": r["tdate"] or ""})
        return out

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
