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
                reason TEXT DEFAULT '',
                comments TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, sample_id)
            );

            CREATE INDEX IF NOT EXISTS idx_votes_sample ON votes(sample_id);
            CREATE INDEX IF NOT EXISTS idx_votes_user ON votes(user_id);

            -- Analysis evaluation module tables
            CREATE TABLE IF NOT EXISTS a_galaxies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                galaxy_name TEXT NOT NULL UNIQUE,
                image_path TEXT NOT NULL,
                analysis_text TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS a_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                galaxy_id INTEGER NOT NULL REFERENCES a_galaxies(id),
                residual_desc_rating INTEGER NOT NULL,
                reasoning_rating INTEGER NOT NULL,
                overall_rating INTEGER NOT NULL,
                feedback TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, galaxy_id)
            );

            CREATE INDEX IF NOT EXISTS idx_a_eval_galaxy ON a_evaluations(galaxy_id);
            CREATE INDEX IF NOT EXISTS idx_a_eval_user ON a_evaluations(user_id);
        ''')
        db.commit()

        # Migration: add reason column if not exists
        try:
            db.execute("ALTER TABLE votes ADD COLUMN reason TEXT DEFAULT ''")
            db.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: add summary_path column if not exists
        try:
            db.execute("ALTER TABLE rounds ADD COLUMN summary_path TEXT")
            db.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
