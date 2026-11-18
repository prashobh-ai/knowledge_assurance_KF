"""SQLite store: assets (with two sets of dates), chunks, keyword index, links."""
import os
import sqlite3

DB_PATH = os.path.join("build", "kfabric.db")

SCHEMA = """
CREATE TABLE assets(
  id TEXT PRIMARY KEY, path TEXT, title TEXT, ktype TEXT, owner TEXT, topic TEXT,
  sha TEXT, outcome TEXT, trust REAL, acl TEXT,
  valid_from TEXT, valid_to TEXT,          -- when the content applies in the business
  recorded_at TEXT, superseded_at TEXT,    -- when the fabric learned / replaced it
  relation TEXT, relates_to TEXT, evidence TEXT);
CREATE TABLE chunks(id INTEGER PRIMARY KEY, asset_id TEXT, seq INTEGER, text TEXT);
CREATE VIRTUAL TABLE fts USING fts5(text);
CREATE TABLE links(src TEXT, dst TEXT, kind TEXT);
"""


def connect(path=DB_PATH, fresh=False):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if fresh and os.path.exists(path):
        os.remove(path)
    new = not os.path.exists(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    if new:
        con.executescript(SCHEMA)
    return con
