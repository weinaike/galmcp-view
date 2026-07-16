import os
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
                source TEXT NOT NULL DEFAULT 'default',
                galaxy_id TEXT NOT NULL,
                num_rounds INTEGER NOT NULL DEFAULT 0,
                last_scanned TEXT DEFAULT (datetime('now')),
                UNIQUE(source, galaxy_id)
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
                image_desc_rating INTEGER NOT NULL DEFAULT 0,
                residual_desc_rating INTEGER NOT NULL DEFAULT 0,
                component_pred_rating INTEGER NOT NULL DEFAULT 0,
                feedback TEXT DEFAULT '',
                image_desc_feedback TEXT DEFAULT '',
                residual_desc_feedback TEXT DEFAULT '',
                component_pred_feedback TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, galaxy_id)
            );

            CREATE INDEX IF NOT EXISTS idx_a_eval_galaxy ON a_evaluations(galaxy_id);
            CREATE INDEX IF NOT EXISTS idx_a_eval_user ON a_evaluations(user_id);

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL UNIQUE,
                container_path TEXT NOT NULL,
                parent_dir TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- visualRAG KB ingestion staging: one draft per (source, galaxy, round),
            -- produced by batch /distill pre-ingest, committed to the live KB on
            -- expert review. Status draft -> committed.
            CREATE TABLE IF NOT EXISTS kb_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_id TEXT NOT NULL,
                source TEXT NOT NULL,
                galaxy_id TEXT NOT NULL,
                round_number INTEGER,
                timestamp_dir TEXT NOT NULL,
                library TEXT,
                distilled_json TEXT,
                final_labels_json TEXT DEFAULT '[]',
                signature_json TEXT DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'draft',
                committed_kb_id TEXT,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(source, galaxy_id, timestamp_dir)
            );

            -- Fitting-scoring ground-truth label sets (拟合评分). One label set
            -- holds the expected component vocabulary per galaxy_id, imported
            -- from a pasted JSON string. Compared against a data source's
            -- best_turn components to produce an accuracy score.
            CREATE TABLE IF NOT EXISTS label_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                galaxy_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS label_galaxies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label_set_id INTEGER NOT NULL REFERENCES label_sets(id),
                galaxy_id TEXT NOT NULL,
                components_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(label_set_id, galaxy_id)
            );

            CREATE INDEX IF NOT EXISTS idx_label_galaxies_set
                ON label_galaxies(label_set_id);
        ''')
        db.commit()

        # Migration: add signature_json to kb_staging (component_signature draft
        # store; mirrors final_labels_json). Idempotent for existing dbs.
        try:
            db.execute("ALTER TABLE kb_staging ADD COLUMN signature_json TEXT DEFAULT '[]'")
            db.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: add source column to samples and rebuild UNIQUE constraint
        cols = [row[1] for row in db.execute('PRAGMA table_info(samples)').fetchall()]
        if 'source' not in cols:
            db.executescript('''
                CREATE TABLE samples_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL DEFAULT 'default',
                    galaxy_id TEXT NOT NULL,
                    num_rounds INTEGER NOT NULL DEFAULT 0,
                    last_scanned TEXT DEFAULT (datetime('now')),
                    UNIQUE(source, galaxy_id)
                );
                INSERT INTO samples_new (id, source, galaxy_id, num_rounds, last_scanned)
                    SELECT id, 'default', galaxy_id, num_rounds, last_scanned FROM samples;
                DROP TABLE samples;
                ALTER TABLE samples_new RENAME TO samples;
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

        # Migration: add bic column to rounds
        try:
            db.execute("ALTER TABLE rounds ADD COLUMN bic REAL")
            db.commit()
        except sqlite3.OperationalError:
            pass

        # Migration: add missing rating columns to a_evaluations
        for col in ('image_desc_rating', 'residual_desc_rating', 'component_pred_rating'):
            try:
                db.execute(f"ALTER TABLE a_evaluations ADD COLUMN {col} INTEGER DEFAULT 0")
                db.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Migration: add per-dimension feedback columns to a_evaluations
        for col in ('image_desc_feedback', 'residual_desc_feedback', 'component_pred_feedback'):
            try:
                db.execute(f"ALTER TABLE a_evaluations ADD COLUMN {col} TEXT DEFAULT ''")
                db.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Seed sources table if empty
        source_count = db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
        if source_count == 0:
            env_sources = current_app.config.get('GALFIT_SOURCES', {})
            if env_sources:
                for label, path in env_sources.items():
                    parent = current_app.config.get('GALFIT_PARENT_DIRS', {})
                    pd = ''
                    for pname, ppath in parent.items():
                        if path.startswith(ppath):
                            pd = pname
                            break
                    db.execute(
                        'INSERT OR IGNORE INTO sources (label, container_path, parent_dir) VALUES (?, ?, ?)',
                        (label, path, pd)
                    )
            else:
                existing = db.execute('SELECT DISTINCT source FROM samples').fetchall()
                parent_dirs = current_app.config.get('GALFIT_PARENT_DIRS', {})
                for row in existing:
                    label = row[0]
                    for pname, ppath in parent_dirs.items():
                        candidate = os.path.join(ppath, label)
                        if os.path.isdir(candidate):
                            db.execute(
                                'INSERT OR IGNORE INTO sources (label, container_path, parent_dir) VALUES (?, ?, ?)',
                                (label, candidate, pname)
                            )
                            break
            db.commit()

        # Migration: add sort_order column to sources
        try:
            db.execute("ALTER TABLE sources ADD COLUMN sort_order INTEGER DEFAULT 0")
            db.commit()
        except sqlite3.OperationalError:
            pass

        # Migration: add description column to sources
        try:
            db.execute("ALTER TABLE sources ADD COLUMN description TEXT DEFAULT ''")
            db.commit()
        except sqlite3.OperationalError:
            pass

        # Migration: add fitting_type column to samples
        try:
            db.execute("ALTER TABLE samples ADD COLUMN fitting_type TEXT DEFAULT 'single-band'")
            db.commit()
        except sqlite3.OperationalError:
            pass

        # Migration: add best_turn column to samples (AI-recommended timestamp_dir,
        # parsed from analysis_report.md by the scanner; self-heals lazily in app.py
        # for rows scanned before this migration landed).
        try:
            db.execute("ALTER TABLE samples ADD COLUMN best_turn TEXT")
            db.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: add best_components column to samples (the component type-name
        # list that accompanies best_turn in the same analysis_report.md JSON block,
        # e.g. ["Disk","Bar","Companion"]; stored as a JSON string). Self-heals
        # lazily in app.py alongside best_turn.
        try:
            db.execute("ALTER TABLE samples ADD COLUMN best_components TEXT")
            db.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: add multi-band columns to rounds
        for col, coldef in [
            ('round_status', "TEXT DEFAULT 'success'"),
            ('per_band_chi2_json', 'TEXT'),
            ('image_fit_path', 'TEXT'),
            ('fitting_type', "TEXT DEFAULT 'single-band'"),
            ('is_sed', 'INTEGER DEFAULT 0'),
        ]:
            try:
                db.execute(f"ALTER TABLE rounds ADD COLUMN {col} {coldef}")
                db.commit()
            except sqlite3.OperationalError:
                pass
