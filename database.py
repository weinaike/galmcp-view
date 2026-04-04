import sqlite3
from flask import g, current_app


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db(app):
    with app.app_context():
        db = get_db()
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                galaxy_id TEXT NOT NULL UNIQUE,
                num_rounds INTEGER NOT NULL DEFAULT 0,
                last_scanned TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_id INTEGER NOT NULL REFERENCES samples(id),
                round_number INTEGER NOT NULL,
                timestamp_dir TEXT NOT NULL,
                png_path TEXT,
                chi_squared_nu REAL,
                components_json TEXT,
                UNIQUE(sample_id, round_number)
            );

            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                sample_id INTEGER NOT NULL REFERENCES samples(id),
                is_perfect INTEGER NOT NULL,
                best_round INTEGER,
                comments TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, sample_id)
            );

            CREATE INDEX IF NOT EXISTS idx_votes_sample ON votes(sample_id);
            CREATE INDEX IF NOT EXISTS idx_votes_user ON votes(user_id);
        ''')
        db.commit()
