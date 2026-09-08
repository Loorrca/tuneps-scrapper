"""
Ouvrir une base existante après une mise à jour du code.

Tous les autres tests partent d'une base neuve, où le schéma est créé d'un coup
et où rien ne manque jamais. C'est précisément l'angle mort qui a mis
l'interface du Raspberry Pi hors service : un `CREATE INDEX` placé dans le
schéma portait sur une colonne que la migration n'avait pas encore ajoutée. Sur
une base neuve la colonne existait, tout passait ; sur la base de production
elle n'existait pas, `Store()` levait « no such column », et chaque requête
répondait 500.

Ce fichier rejoue donc les schémas des versions PRÉCÉDENTES, avec des données
dedans, et vérifie qu'une base ainsi créée s'ouvre et reste exploitable. Toute
future colonne doit venir avec sa version ici.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuneps.store import Store  # noqa: E402

# --- v1 : avant l'interface web. notices sans status/status_at/note.
V1 = """
CREATE TABLE notices (uid TEXT PRIMARY KEY, source TEXT NOT NULL, number TEXT NOT NULL,
 mod_seq TEXT NOT NULL, master_id INTEGER, title_fr TEXT, title_ar TEXT, title_en TEXT,
 buyer TEXT, published_at TEXT, deadline_at TEXT, url TEXT, confidence TEXT, score REAL,
 terms TEXT, first_seen TEXT NOT NULL, notified_at TEXT);
CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, ended_at TEXT,
 mode TEXT, scanned INTEGER, matched INTEGER, new_hits INTEGER, ok INTEGER, detail TEXT);
"""

# --- v2 : interface web et résultats, avant le module « prix marché ».
V2 = V1.replace(
    "terms TEXT, first_seen TEXT NOT NULL, notified_at TEXT);",
    "terms TEXT, first_seen TEXT NOT NULL, notified_at TEXT,"
    " status TEXT NOT NULL DEFAULT 'pending', status_at TEXT, note TEXT);"
) + """
CREATE INDEX idx_notices_status ON notices(status);
CREATE TABLE results (uid TEXT PRIMARY KEY, checked_at TEXT NOT NULL,
 published INTEGER NOT NULL DEFAULT 0, winner_declared INTEGER NOT NULL DEFAULT 0, detail TEXT);
"""

# --- v3 : première version de « prix marché ». Ni competitors.reg_no, ni
# market_tenders.lot — c'est la base qui tournait sur le Pi.
V3 = V2 + """
CREATE TABLE articles (id INTEGER PRIMARY KEY, label TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '');
CREATE TABLE competitors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
 norm TEXT NOT NULL UNIQUE, is_us INTEGER NOT NULL DEFAULT 0, created_at TEXT);
CREATE TABLE competitor_aliases (norm TEXT PRIMARY KEY, competitor_id INTEGER NOT NULL,
 raw TEXT, seen_at TEXT);
CREATE INDEX idx_alias_comp ON competitor_aliases(competitor_id);
CREATE TABLE market_tenders (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT,
 ref TEXT NOT NULL DEFAULT '', buyer TEXT NOT NULL DEFAULT '', tdate TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '', created_at TEXT);
CREATE INDEX idx_mt_uid ON market_tenders(uid);
CREATE TABLE tender_items (tender_id INTEGER NOT NULL, article_id INTEGER NOT NULL,
 qty REAL NOT NULL, PRIMARY KEY (tender_id, article_id));
CREATE TABLE bids (tender_id INTEGER NOT NULL, competitor_id INTEGER NOT NULL,
 amount REAL NOT NULL, PRIMARY KEY (tender_id, competitor_id));
CREATE TABLE our_prices (article_id INTEGER PRIMARY KEY, price REAL NOT NULL);
"""

VERSIONS = {
    "v1 (avant l'interface web)": V1,
    "v2 (interface web + résultats)": V2,
    "v3 (première version de prix marché)": V3,
}


def _peupler(db: Path, schema: str) -> None:
    """Une base ancienne AVEC des données : une base vide masquerait les ennuis."""
    c = sqlite3.connect(db)
    c.executescript(schema)
    c.execute("""INSERT INTO notices (uid, source, number, mod_seq, title_fr, buyer,
                 published_at, deadline_at, url, confidence, score, terms, first_seen)
                 VALUES ('ao:1:00','ao','20260100001','00','Drapeaux','Sousse',
                 '2026-01-05','2026-02-01','http://x','high',3.0,'["drapeau"]','2026-01-06')""")
    if "articles" in schema:
        c.executemany("INSERT INTO articles (id,label,unit) VALUES (?,?,'')",
                      [(i, f"Article {i}") for i in range(1, 17)])
        c.execute("INSERT INTO competitors (name, norm) VALUES ('ALPHA TEXTILE','ALPHA TEXTILE')")
        c.execute("INSERT INTO market_tenders (uid, ref, buyer, tdate) "
                  "VALUES ('ao:1:00','AO-1','Sousse','2026-01-05')")
        c.execute("INSERT INTO tender_items (tender_id, article_id, qty) VALUES (1,1,10)")
        c.execute("INSERT INTO bids (tender_id, competitor_id, amount) VALUES (1,1,5000)")
    c.commit()
    c.close()


def run() -> int:
    fails: list[str] = []

    for label, schema in VERSIONS.items():
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ancienne.db"
            _peupler(db, schema)
            try:
                st = Store(db)
            except Exception as e:  # noqa: BLE001
                fails.append(f"{label} : ouverture impossible — {type(e).__name__} : {e}")
                continue
            try:
                # les colonnes attendues sont toutes là
                for table, needed in (
                    ("notices", {"status", "status_at", "note"}),
                    ("competitors", {"reg_no"}),
                    ("market_tenders", {"lot"}),
                ):
                    cols = {r["name"] for r in st.db.execute(f"PRAGMA table_info({table})")}
                    manque = needed - cols
                    if manque:
                        fails.append(f"{label} : colonnes absentes de {table} : {manque}")

                # les données d'origine ont survécu
                if st.count() != 1:
                    fails.append(f"{label} : les avis existants ont disparu")
                if len(st.articles()) != 16:
                    fails.append(f"{label} : {len(st.articles())} articles au lieu de 16")

                # et la base reste utilisable, pas seulement ouvrable
                st.set_status("ao:1:00", "done")
                if [n["status"] for n in st.list_notices()] != ["done"]:
                    fails.append(f"{label} : écriture du statut impossible")
                st.save_market_tender("AO-2", "Sfax", "2026-02-01", {1: 5},
                                      [{"name": "BETA", "amount": 900, "reg_no": "9999X"}],
                                      uid="ao:2:00", lot="1")
                if ("ao:2:00", "1") not in st.market_keys():
                    fails.append(f"{label} : le lot n'est pas enregistré après migration")
                if not any(c["reg_no"] == "9999X" for c in st.competitors()):
                    fails.append(f"{label} : le matricule fiscal n'est pas enregistré")

                # ouvrir deux fois de suite ne doit rien casser non plus
                st.close()
                st = Store(db)
                if st.count() != 1:
                    fails.append(f"{label} : seconde ouverture instable")
            except Exception as e:  # noqa: BLE001
                fails.append(f"{label} : base ouverte mais inutilisable — "
                             f"{type(e).__name__} : {e}")
            finally:
                try:
                    st.close()
                except Exception:  # noqa: BLE001
                    pass

    print(f"migrations : {len(VERSIONS) - len({f.split(' :')[0] for f in fails})}"
          f"/{len(VERSIONS)} anciennes bases s'ouvrent et restent utilisables")
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
