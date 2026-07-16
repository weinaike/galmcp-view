"""Galaxy Fitting Result Voting Web Application."""

import os
import glob
import json
import re
import statistics as stats_mod
from collections import OrderedDict
from functools import wraps
import markdown
from flask import (Flask, render_template, request, redirect, url_for,
                   session, send_file, abort, Response, jsonify)
from config import Config
from database import get_db, close_db, init_db
from scanner import scan_galaxies, find_analysis_report_path, parse_best_turn


# --- S4G table7 loader ---
_s4g_data = None

def _load_s4g_table():
    global _s4g_data
    if _s4g_data is not None:
        return _s4g_data

    tsv_path = os.path.join(os.path.dirname(__file__), 'static', 's4g_table7.tsv')
    if not os.path.isfile(tsv_path):
        _s4g_data = {}
        return _s4g_data

    data = {}
    with open(tsv_path, 'r', encoding='utf-8') as f:
        # skip comment lines and blank lines to find header
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                header_line = stripped
                break
        next(f)  # units line
        next(f)  # separator line
        headers = header_line.split('\t')

        for line in f:
            parts = line.split('\t')
            row = {}
            for i, h in enumerate(headers):
                if i < len(parts):
                    val = parts[i].strip()
                    row[h] = val if val else None
            name = row.get('Name')
            if name:
                data.setdefault(name, []).append(row)

    _s4g_data = data
    return _s4g_data


def _get_s4g_components(galaxy_id):
    """Get S4G table7 decomposition components for a galaxy."""
    data = _load_s4g_table()
    raw_rows = data.get(galaxy_id, [])

    param_map = {
        'sersic':   ['f1', 'mag1', 'q1', 'PA1', 'Re', 'n'],
        'edgedisk': ['f2', 'mu02', 'PA2', 'hr2', 'hz2'],
        'expdisk':  ['f3', 'mag3', 'q3', 'PA3', 'hr3', 'mu03'],
        'ferrer2':  ['f4', 'mu04', 'q4', 'PA4', 'Rbar'],
        'psf':      ['f5', 'mag5'],
    }

    components = []
    for row in raw_rows:
        fn = (row.get('Fn') or '').strip()
        params = {}
        for k in param_map.get(fn, []):
            if row.get(k):
                params[k] = row[k]
        components.append({
            'type': (row.get('C') or '').strip(),
            'function': fn,
            'params': params,
        })
    return components

app = Flask(__name__)
app.config.from_object(Config)

# Morphology-component vocabulary (mirrors visualRAG retrieval/component_schema.py;
# the expert picks GT labels at ingest-review time).
TAXONOMY = ['disk', 'edge-on disk', 'bulge', 'bar', 'outer disk', 'elliptical', 'fourier', 'psf', 'single sersic', 'companion']


def _pretty(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


app.jinja_env.filters['pretty'] = _pretty


def _get_sources_from_db():
    """Load sources from DB, returning OrderedDict {label: container_path}."""
    db = get_db()
    rows = db.execute(
        'SELECT label, container_path FROM sources ORDER BY sort_order, id'
    ).fetchall()
    return OrderedDict((r['label'], r['container_path']) for r in rows)


def _get_source_path(source_label):
    sources = _get_sources_from_db()
    if source_label not in sources:
        abort(404)
    return sources[source_label]


def _kb_render_teaching(v):
    """Coerce a teaching field to a markdown string.

    The KB ``/distill`` now transcribes ``image_description`` / ``reasoning`` as
    markdown strings. Older drafts (pre-transcription) stored dicts — render
    those as readable ``key: value`` lines so the editor still shows them (they
    self-heal to a clean string on the next save).
    """
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        lines = []
        for k, val in v.items():
            if val in (None, "", []):
                continue
            if isinstance(val, (list, tuple)):
                val = "; ".join(str(x) for x in val)
            lines.append(f"{k}: {val}")
        return "\n".join(lines)
    return "" if v is None else str(v)


def _kb_distilled(row):
    try:
        d = json.loads(row['distilled_json']) if row and row['distilled_json'] else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    # normalize legacy dict teaching fields to markdown strings (new shape)
    for k in ('image_description', 'reasoning'):
        if k in d:
            d[k] = _kb_render_teaching(d[k])
    return d


def _kb_labels(row):
    try:
        return json.loads(row['final_labels_json']) if row and row['final_labels_json'] else []
    except (json.JSONDecodeError, TypeError):
        return []


def _kb_signature(row):
    try:
        return json.loads(row['signature_json']) if row and row['signature_json'] else []
    except (json.JSONDecodeError, TypeError):
        return []


def _round_can_distill(source, galaxy_id, timestamp_dir):
    """True iff the round's archive has a ``*component_analysis*.md`` report —
    the ``/distill`` transcription source (and the comparison PNG). Drives STATE
    A: report present -> show 开始蒸馏; absent -> show 手动填写 (so we never
    offer a distill that would 503). Mirrors kb_client.package_material's glob."""
    if not (source and galaxy_id and timestamp_dir):
        return False
    container_path = _get_sources_from_db().get(source)
    if not container_path:
        return False
    import kb_client
    archive, _obj = kb_client.archive_paths(container_path, galaxy_id, timestamp_dir)
    has_md = bool(glob.glob(os.path.join(archive, "*component_analysis*.md")))
    has_png = bool(glob.glob(os.path.join(archive, "*_galfit_comparison.png"))
                   or glob.glob(os.path.join(archive, "*comparison*.png")))
    return has_md and has_png


def _kb_render(source, galaxy_id, timestamp_dir, round_number, row=None, flash=None):
    """Render the shared kb_panel fragment (the single AJAX render path)."""
    # Only STATE A (no distilled draft) needs the report check; skip the glob/db
    # for B/C renders.
    can_distill = (not (row and row['distilled_json'])) and \
        _round_can_distill(source, galaxy_id, timestamp_dir)
    return render_template(
        '_kb_panel_render.html',
        source=source, galaxy_id=galaxy_id, timestamp_dir=timestamp_dir,
        round_number=round_number, row=row,
        distilled=_kb_distilled(row) if row else {},
        labels=_kb_labels(row) if row else [],
        signature=_kb_signature(row) if row else [],
        taxonomy=TAXONOMY, flash=flash, can_distill=can_distill,
        is_admin=bool(session.get('is_admin')))


@app.context_processor
def inject_sources():
    db = get_db()
    label_sets = db.execute(
        'SELECT id, name, description FROM label_sets ORDER BY id'
    ).fetchall()
    return {'sources': _get_sources_from_db(), 'label_sets': label_sets}


def _load_final_chi2():
    """Load final_chi2.json mapping galaxy_id -> chi2 value."""
    path = os.path.join(os.path.dirname(__file__), 'static', 'final_chi2.json')
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _find_analysis_report_path(galaxy_id, base_path):
    # Forwarded to scanner.find_analysis_report_path (the canonical home,
    # so the scanner can populate samples.best_turn without importing Flask).
    return find_analysis_report_path(galaxy_id, base_path)


def _find_working_note_path(galaxy_id, base_path):
    galaxy_dir = os.path.join(base_path, galaxy_id)
    path = os.path.join(galaxy_dir, 'working_note.md')
    return path if os.path.isfile(path) else None


def _parse_best_turn(galaxy_id, base_path):
    """Forwarded to scanner.parse_best_turn (canonical home).

    Returns a (best_turn, components_list) tuple — see scanner.parse_best_turn.
    """
    return parse_best_turn(galaxy_id, base_path)


def _purify_component_set(components):
    """Normalize a component collection for equality comparison.

    Mirrors the rules used by the compare-page statistics: drop Fourier and
    Companion (decorative/auxiliary), and treat a pure-disk model as a single
    Sersic. Accepts any iterable of names; returns a new ``set``.
    """
    s = set(c for c in components if c)
    s.discard('Fourier')
    s.discard('Companion')
    if 'AGN' in s:
        s.add('Nucleus')
        s.discard('AGN')
    if s == {'Disk'} or s == {'Bulge'} or s == {'Bar'}:
        s = {'SingleSersic'}
    return s


def _loads_components(raw):
    """Decode the samples.best_components JSON string into a list (or [])."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def get_best_turn(sample_row, db):
    """Resolve the AI-recommended best_turn for a sample.

    Strategy:
      1. If samples.best_turn is already populated in the DB -> return it.
      2. Otherwise fall back to parsing analysis_report.md from disk.
      3. On a successful fallback, lazily write the value (and the accompanying
         components list, read from the same JSON block) back to the DB
         (self-heal) so subsequent reads skip the file parse for both.

    `sample_row` must carry 'id', 'source', 'galaxy_id', 'best_turn'.
    Returns the timestamp_dir string, or None.
    """
    if sample_row['best_turn']:
        return sample_row['best_turn']
    base_path = _get_source_path(sample_row['source'])
    bt, comps = _parse_best_turn(sample_row['galaxy_id'], base_path)
    if bt:
        # self-heal: persist best_turn + best_components (same file read) so the
        # next request skips the file parse for both.
        db.execute('UPDATE samples SET best_turn=?, best_components=? WHERE id=?',
                   (bt, json.dumps(comps or []), sample_row['id']))
        db.commit()
    return bt


def get_best_turn_meta(sample_row, db):
    """Resolve (best_turn, components) for a sample.

    Same DB-cache + file-fallback + lazy write-back strategy as get_best_turn,
    but returns both fields. Used by the compare page, which shows the
    component type-name list alongside the best round.

    `sample_row` should carry 'best_components' for the fast path; if the
    column is absent from the row it falls through to a file parse. Returns
    (best_turn_or_None, components_list).
    """
    bt = sample_row['best_turn']
    bc_raw = sample_row['best_components'] if 'best_components' in sample_row.keys() else None
    if bt and bc_raw:
        return bt, _loads_components(bc_raw)
    base_path = _get_source_path(sample_row['source'])
    parsed_bt, parsed_comps = _parse_best_turn(sample_row['galaxy_id'], base_path)
    if not parsed_bt:
        # no file data — return whatever was already cached
        return bt, (_loads_components(bc_raw) if bc_raw else [])
    comps = parsed_comps or []
    db.execute('UPDATE samples SET best_turn=?, best_components=? WHERE id=?',
               (parsed_bt, json.dumps(comps), sample_row['id']))
    db.commit()
    return parsed_bt, comps


# Relative χ²/ν drop a diagnosis round must produce at the NEXT round to be
# admitted as a verified-diagnosis teaching case (matches the skill's 5% margin:
# only rounds whose report actually helped get distilled, not drift/noise).
KB_IMPROVED_MARGIN = 0.05


def _stage_distill(db, source, galaxy_id, round_row, library, distilled, err):
    """Upsert one /distill result into kb_staging AND commit it immediately.
    Returns 'ok' | 'fail' | 'skip'.

    'skip' = a successful (or committed) draft already exists for this
    (source, galaxy, ts) — re-running batch leaves finished rows alone. FAILED
    drafts (error, no distilled content) are retried+overwritten, so transient
    VLM/KB failures self-heal on the next batch run instead of sticking as empty
    rows. ``library`` ('perfect' | 'problem') is chosen by the caller's policy.

    Commits per row (not once at the end): an interruption (browser closed,
    proxy timeout) keeps every draft already distilled and never wastes a VLM
    call on a result that got rolled back. Under WAL the committed rows are
    visible to other connections right away — so another tab polling /kb/review
    sees drafts appear one by one during the batch."""
    ts = round_row['timestamp_dir']
    existing = db.execute(
        'SELECT status, distilled_json FROM kb_staging '
        'WHERE source=? AND galaxy_id=? AND timestamp_dir=?',
        (source, galaxy_id, ts)).fetchone()
    if existing and (existing['status'] == 'committed' or existing['distilled_json']):
        return 'skip'
    sample_id = f"{galaxy_id}_{ts}"
    if distilled:
        db.execute(
            'INSERT INTO kb_staging '
            '(sample_id, source, galaxy_id, round_number, timestamp_dir, library, '
            'distilled_json, status, error) '
            'VALUES (?,?,?,?,?,?,?, \'draft\', NULL) '
            'ON CONFLICT(source, galaxy_id, timestamp_dir) DO UPDATE SET '
            'library=excluded.library, distilled_json=excluded.distilled_json, '
            "status='draft', error=NULL, updated_at=datetime('now')",
            (sample_id, source, galaxy_id, round_row['round_number'], ts,
             library, json.dumps(distilled, ensure_ascii=False)))
        db.commit()
        return 'ok'
    db.execute(
        'INSERT INTO kb_staging '
        '(sample_id, source, galaxy_id, round_number, timestamp_dir, library, '
        'status, error) '
        "VALUES (?,?,?,?,?,?, 'draft', ?) "
        'ON CONFLICT(source, galaxy_id, timestamp_dir) DO UPDATE SET '
        "status='draft', error=excluded.error, updated_at=datetime('now')",
        (sample_id, source, galaxy_id, round_row['round_number'], ts,
         library, err or '蒸馏失败（KB 服务或 VLM 错误）'))
    db.commit()
    return 'fail'




@app.route('/')
def index():
    return redirect(url_for('login'))


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# --- Auth routes ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()[:50]
        if not username:
            return render_template('login.html', error='请输入用户名')
        db = get_db()
        db.execute('INSERT OR IGNORE INTO users (username) VALUES (?)', (username,))
        db.commit()
        user = db.execute('SELECT id, username FROM users WHERE username = ?',
                          (username,)).fetchone()
        session['user_id'] = user['id']
        session['username'] = user['username']
        return redirect('/voting/')
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


# --- Sample list ---

@app.route('/voting/')
@app.route('/voting/<source>/')
@login_required
def sample_list(source=None):
    sources = _get_sources_from_db()
    if source is None:
        first = next(iter(sources))
        return redirect(url_for('sample_list', source=first))
    if source not in sources:
        abort(404)

    db = get_db()
    user_id = session['user_id']

    samples = db.execute('''
        SELECT s.galaxy_id, s.num_rounds, s.source,
               v.is_perfect, v.best_round, v.reason, v.comments,
               (SELECT COUNT(*) FROM votes WHERE sample_id = s.id) AS total_votes
        FROM samples s
        LEFT JOIN votes v ON v.sample_id = s.id AND v.user_id = ?
        WHERE s.source = ?
        ORDER BY s.galaxy_id
    ''', (user_id, source)).fetchall()

    total = len(samples)
    evaluated = sum(1 for s in samples if s['is_perfect'] is not None)

    return render_template('sample_list.html',
                           samples=samples, total=total, evaluated=evaluated,
                           current_source=source)


# --- Sample detail ---

@app.route('/sample/<galaxy_id>')
@login_required
def sample_detail_legacy(galaxy_id):
    db = get_db()
    row = db.execute('SELECT source FROM samples WHERE galaxy_id = ?', (galaxy_id,)).fetchone()
    if not row:
        abort(404)
    return redirect(url_for('sample_detail', source=row['source'], galaxy_id=galaxy_id))


@app.route('/sample/<source>/<galaxy_id>')
@login_required
def sample_detail(source, galaxy_id):
    base_path = _get_source_path(source)
    db = get_db()
    user_id = session['user_id']

    sample = db.execute('SELECT * FROM samples WHERE source = ? AND galaxy_id = ?',
                        (source, galaxy_id)).fetchone()
    if not sample:
        abort(404)

    fitting_type = sample['fitting_type'] if sample['fitting_type'] else 'single-band'
    data_dir_name = 'output' if fitting_type == 'multi-band' else 'archives'

    # Check if analysis report exists
    report_path = _find_analysis_report_path(galaxy_id, base_path)
    has_analysis_report = report_path is not None
    has_working_note = _find_working_note_path(galaxy_id, base_path) is not None

    # Check for *_comparison.png in galaxy directory
    comparison_png = None
    galaxy_dir = os.path.join(base_path, galaxy_id)
    try:
        comparison_png = next(
            (f for f in os.listdir(galaxy_dir) if f.endswith('_comparison.png')),
            None
        )
    except OSError:
        pass

    rounds = db.execute('''
        SELECT id, round_number, timestamp_dir, png_path, chi_squared_nu, bic,
               components_json, summary_path, fitting_type, round_status, is_sed,
               per_band_chi2_json, image_fit_path
        FROM rounds
        WHERE sample_id = ?
        ORDER BY round_number
    ''', (sample['id'],)).fetchall()

    rounds_data = []
    for r in rounds:
        round_fitting_type = r['fitting_type'] if r['fitting_type'] else fitting_type
        round_data_dir = 'output' if round_fitting_type == 'multi-band' else 'archives'

        # Find fit log / component attributes.
        # 单波段读取 fit.log；多波段读取 component_attributes.txt
        # （run.log 充斥 astropy 警告与单位换算日志，对评估拟合帮助不大）。
        log_name = 'component_attributes.txt' if round_fitting_type == 'multi-band' else 'fit.log'
        fit_log_path = os.path.join(base_path, galaxy_id, round_data_dir, r['timestamp_dir'], log_name)
        fit_log_content = ''
        if os.path.isfile(fit_log_path):
            try:
                with open(fit_log_path, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                if round_fitting_type == 'single-band':
                    # 跳过第 2-6 行（索引 1-5）：空行 + 文件路径信息
                    fit_log_content = ''.join(lines[:1] + lines[6:])
                else:
                    fit_log_content = ''.join(lines)
            except OSError:
                pass

        # Check for component analysis file (*_component_analysis.md)
        archive_dir = os.path.join(base_path, galaxy_id, round_data_dir, r['timestamp_dir'])
        comp_analysis_file = None
        try:
            comp_analysis_file = next(
                (f for f in os.listdir(archive_dir) if 'component_analysis' in f and f.endswith('.md')),
                None
            )
        except OSError:
            pass

        # Parse per-band chi2 from DB
        per_band_chi2 = None
        if r['per_band_chi2_json']:
            try:
                per_band_chi2 = json.loads(r['per_band_chi2_json'])
            except (json.JSONDecodeError, TypeError):
                pass

        round_status = r['round_status'] if r['round_status'] else 'success'
        is_sed = bool(r['is_sed']) if r['is_sed'] is not None else False
        has_image_fit = bool(r['image_fit_path']) if r['image_fit_path'] else False

        # KB staging state for this round (drives the state-adaptive entry
        # button label: None=蒸馏, draft=查看蒸馏, committed=已入库).
        kb_row = db.execute(
            'SELECT status, library FROM kb_staging '
            'WHERE source=? AND galaxy_id=? AND timestamp_dir=?',
            (source, galaxy_id, r['timestamp_dir'])).fetchone()
        kb_status = kb_row['status'] if kb_row else None

        rounds_data.append({
            'round_number': r['round_number'],
            'timestamp_dir': r['timestamp_dir'],
            'has_png': r['png_path'] is not None,
            'has_summary': r['summary_path'] is not None,
            'has_comp_analysis': comp_analysis_file is not None,
            'comp_analysis_path': os.path.join(archive_dir, comp_analysis_file) if comp_analysis_file else '',
            'chi_squared_nu': r['chi_squared_nu'],
            'bic': r['bic'],
            'fit_log': fit_log_content,
            'per_band_chi2': per_band_chi2,
            'round_status': round_status,
            'is_sed': is_sed,
            'has_image_fit': has_image_fit,
            'kb_status': kb_status,
        })

    my_vote = db.execute('''
        SELECT is_perfect, best_round, reason, comments
        FROM votes WHERE user_id = ? AND sample_id = ?
    ''', (user_id, sample['id'])).fetchone()

    prev_sample = db.execute('''
        SELECT s.galaxy_id FROM samples s
        WHERE s.source = ? AND s.galaxy_id < ?
        ORDER BY s.galaxy_id DESC LIMIT 1
    ''', (source, galaxy_id)).fetchone()

    next_sample = db.execute('''
        SELECT s.galaxy_id FROM samples s
        WHERE s.source = ? AND s.galaxy_id > ? AND s.id NOT IN
            (SELECT sample_id FROM votes WHERE user_id = ?)
        ORDER BY s.galaxy_id LIMIT 1
    ''', (source, galaxy_id, user_id)).fetchone()

    import kb_client
    kb_enabled = kb_client.enabled()

    return render_template('sample_detail.html',
                           source=source,
                           current_source=source,
                           kb_enabled=kb_enabled,
                           galaxy_id=galaxy_id,
                           sample=sample,
                           rounds=rounds_data,
                           my_vote=my_vote,
                           fitting_type=fitting_type,
                           has_analysis_report=has_analysis_report,
                           has_working_note=has_working_note,
                           has_comparison_png=comparison_png is not None,
                           s4g_components=_get_s4g_components(galaxy_id),
                           best_turn=get_best_turn(sample, db),
                           final_chi2=_load_final_chi2().get(galaxy_id),
                           prev_sample=prev_sample['galaxy_id'] if prev_sample else None,
                           next_sample=next_sample['galaxy_id'] if next_sample else None)


@app.route('/sample/<source>/<galaxy_id>/vote', methods=['POST'])
@login_required
def submit_vote(source, galaxy_id):
    db = get_db()
    user_id = session['user_id']

    sample = db.execute('SELECT id FROM samples WHERE source = ? AND galaxy_id = ?',
                        (source, galaxy_id)).fetchone()
    if not sample:
        abort(404)

    is_perfect = int(request.form.get('is_perfect', 0))
    best_round = request.form.get('best_round', '').strip()
    reason = request.form.get('reason', '').strip()
    comments = request.form.get('comments', '').strip()

    if is_perfect == 1:
        if not best_round:
            return redirect(url_for('sample_detail', source=source, galaxy_id=galaxy_id))
        best_round = int(best_round)
    else:
        best_round = None
        reason = None

    db.execute('''
        INSERT INTO votes (user_id, sample_id, is_perfect, best_round, reason, comments, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, sample_id) DO UPDATE SET
            is_perfect=excluded.is_perfect,
            best_round=excluded.best_round,
            reason=excluded.reason,
            comments=excluded.comments,
            updated_at=datetime('now')
    ''', (user_id, sample['id'], is_perfect, best_round, reason, comments))
    db.commit()

    return redirect(url_for('sample_detail', source=source, galaxy_id=galaxy_id))


# --- Image serving ---

@app.route('/image/<source>/<galaxy_id>/<timestamp_dir>')
@login_required
def serve_image(source, galaxy_id, timestamp_dir):
    db = get_db()
    row = db.execute('''
        SELECT r.png_path FROM rounds r
        JOIN samples s ON r.sample_id = s.id
        WHERE s.source = ? AND s.galaxy_id = ? AND r.timestamp_dir = ?
    ''', (source, galaxy_id, timestamp_dir)).fetchone()

    if not row or not row['png_path']:
        abort(404)

    png_path = row['png_path']
    if not os.path.isfile(png_path):
        abort(404)

    return send_file(png_path, mimetype='image/png')


@app.route('/image-fit/<source>/<galaxy_id>/<timestamp_dir>')
@login_required
def serve_image_fit(source, galaxy_id, timestamp_dir):
    db = get_db()
    row = db.execute('''
        SELECT r.image_fit_path FROM rounds r
        JOIN samples s ON r.sample_id = s.id
        WHERE s.source = ? AND s.galaxy_id = ? AND r.timestamp_dir = ?
    ''', (source, galaxy_id, timestamp_dir)).fetchone()

    if not row or not row['image_fit_path']:
        abort(404)

    if not os.path.isfile(row['image_fit_path']):
        abort(404)

    return send_file(row['image_fit_path'], mimetype='image/png')


@app.route('/comparison-image/<source>/<galaxy_id>')
@login_required
def serve_comparison_image(source, galaxy_id):
    galaxy_dir = os.path.join(_get_source_path(source), galaxy_id)
    try:
        filename = next(
            (f for f in os.listdir(galaxy_dir) if f.endswith('_comparison.png')),
            None
        )
    except OSError:
        filename = None
    if not filename:
        abort(404)
    return send_file(os.path.join(galaxy_dir, filename), mimetype='image/png')


# --- Analysis report serving ---

@app.route('/analysis-report/<source>/<galaxy_id>')
@login_required
def serve_analysis_report(source, galaxy_id):
    report_path = _find_analysis_report_path(galaxy_id, _get_source_path(source))
    if not report_path or not os.path.isfile(report_path):
        abort(404)
    with open(report_path, 'r', encoding='utf-8') as f:
        content = f.read()
    html = markdown.markdown(content, extensions=['tables', 'fenced_code', 'toc'])
    return Response(html, mimetype='text/html; charset=utf-8')


# --- Working note serving ---

@app.route('/working-note/<source>/<galaxy_id>')
@login_required
def serve_working_note(source, galaxy_id):
    note_path = _find_working_note_path(galaxy_id, _get_source_path(source))
    if not note_path:
        abort(404)
    with open(note_path, 'r', encoding='utf-8') as f:
        content = f.read()
    html = markdown.markdown(content, extensions=['tables', 'fenced_code', 'toc'])
    return Response(html, mimetype='text/html; charset=utf-8')


# --- Summary log serving ---

@app.route('/summary/<source>/<galaxy_id>/<timestamp_dir>')
@login_required
def serve_summary(source, galaxy_id, timestamp_dir):
    db = get_db()
    row = db.execute('''
        SELECT r.summary_path, r.timestamp_dir FROM rounds r
        JOIN samples s ON r.sample_id = s.id
        WHERE s.source = ? AND s.galaxy_id = ? AND r.timestamp_dir = ?
    ''', (source, galaxy_id, timestamp_dir)).fetchone()

    if not row:
        abort(404)

    summary_path = row['summary_path']
    if not summary_path or not os.path.isfile(summary_path):
        abort(404)

    with open(summary_path, 'r', encoding='utf-8') as f:
        content = f.read()

    return Response(content, mimetype='text/plain; charset=utf-8')


# --- Component analysis serving ---

@app.route('/component-analysis/<source>/<galaxy_id>/<timestamp_dir>')
@login_required
def serve_component_analysis(source, galaxy_id, timestamp_dir):
    db = get_db()
    sample = db.execute(
        'SELECT fitting_type FROM samples WHERE source = ? AND galaxy_id = ?',
        (source, galaxy_id)
    ).fetchone()
    if not sample:
        abort(404)

    fitting_type = sample['fitting_type'] if sample['fitting_type'] else 'single-band'
    data_dir = 'output' if fitting_type == 'multi-band' else 'archives'
    archive_dir = os.path.join(_get_source_path(source),
                               galaxy_id, data_dir, timestamp_dir)
    try:
        filename = next(
            (f for f in os.listdir(archive_dir) if 'component_analysis' in f and f.endswith('.md')),
            None
        )
    except OSError:
        filename = None

    if not filename:
        abort(404)

    filepath = os.path.join(archive_dir, filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    html = markdown.markdown(content, extensions=['tables', 'fenced_code', 'toc'])
    return Response(html, mimetype='text/html; charset=utf-8')


# --- Statistics ---

@app.route('/statistics')
@app.route('/statistics/<source>')
@login_required
def statistics(source=None):
    sources = _get_sources_from_db()
    if source is None:
        first = next(iter(sources))
        return redirect(url_for('statistics', source=first))
    if source not in sources:
        abort(404)

    db = get_db()

    users = db.execute('SELECT id, username FROM users ORDER BY username').fetchall()

    raw_samples = db.execute('''
        SELECT s.id, s.galaxy_id, s.num_rounds,
               (SELECT COUNT(*) FROM votes WHERE sample_id = s.id) AS total_votes,
               (SELECT COUNT(*) FROM votes WHERE sample_id = s.id AND is_perfect = 1) AS perfect_count
        FROM samples s
        WHERE s.source = ?
        ORDER BY s.galaxy_id
    ''', (source,)).fetchall()

    # Build enriched sample data
    samples = []
    vote_data = {}
    for s in raw_samples:
        votes = db.execute('''
            SELECT u.username, v.is_perfect, v.best_round, v.reason, v.comments
            FROM votes v JOIN users u ON v.user_id = u.id
            WHERE v.sample_id = ?
            ORDER BY u.username
        ''', (s['id'],)).fetchall()

        total = s['total_votes']
        perfect = s['perfect_count']

        # Round distribution
        round_dist = {}
        for v in votes:
            if v['best_round'] is not None:
                r = v['best_round']
                round_dist[r] = round_dist.get(r, 0) + 1

        # Format percentage
        if total > 0:
            perfect_pct = f'{perfect / total * 100:.0f}%'
        else:
            perfect_pct = '-'

        # Format round preferences
        if round_dist:
            sorted_rounds = sorted(round_dist.items(), key=lambda x: x[1], reverse=True)
            round_pref = ' '.join(f'R{r}({c}票)' for r, c in sorted_rounds)
            consensus = f'R{sorted_rounds[0][0]} ({sorted_rounds[0][1]}票)'
        else:
            round_pref = '-'
            consensus = '-'

        samples.append({
            'galaxy_id': s['galaxy_id'],
            'num_rounds': s['num_rounds'],
            'total_votes': total,
            'perfect_count': perfect,
            'perfect_pct': perfect_pct,
            'round_pref': round_pref,
            'consensus': consensus,
        })

        vote_data[s['galaxy_id']] = {'votes': votes}

    # Completion matrix — use lists instead of sets for Jinja2 compatibility
    matrix = {}
    for u in users:
        user_votes = db.execute(
            'SELECT s.galaxy_id FROM votes v '
            'JOIN samples s ON v.sample_id = s.id '
            'WHERE v.user_id = ? AND s.source = ?', (u['id'], source)
        ).fetchall()
        matrix[u['username']] = [v['galaxy_id'] for v in user_votes]

    return render_template('statistics.html',
                           users=users, samples=samples,
                           vote_data=vote_data, matrix=matrix,
                           current_source=source)


# --- Fitting Comparison (拟合对比) ---

def _clean_num(s):
    """Strip {}, [], * wrappers from a value string."""
    return re.sub(r'[{}\[\]*]', '', s).strip()


def _safe_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


_FITLOG_COMP_RE = re.compile(
    r'^\s*(sersic|expdisk)\s*:\s*'
    r'\(\s*([^,)]+),\s*([^)]+)\)\s*(.*)$'
)


def _parse_fitlog_params(text):
    """Parse single-band fit.log text into a list of component dicts.

    Only ``sersic`` / ``expdisk`` lines are extracted.  ``expdisk`` has no n
    (always 1), so its ``n`` is set to ``None``.  Values may be wrapped in
    ``{}``, ``[]``, or ``*`` — these are stripped.  Result is sorted by Re
    (descending).
    """
    comps = []
    for line in text.splitlines():
        m = _FITLOG_COMP_RE.match(line)
        if not m:
            continue
        comp_type = m.group(1)
        x = _clean_num(m.group(2))
        y = _clean_num(m.group(3))
        rest = [_clean_num(v) for v in m.group(4).split()]
        if comp_type == 'expdisk':
            if len(rest) < 4:          # mag, re, ba, pa
                continue
            mag, re_val, ba, pa = rest[:4]
            n = '1'                     # expdisk n is always 1
        else:                          # sersic: mag, re, n, ba, pa
            if len(rest) < 5:
                continue
            mag, re_val, n, ba, pa = rest[:5]
        comps.append({'name': comp_type, 'x': x, 'y': y, 'mag': mag,
                      're': re_val, 'n': n, 'ba': ba, 'pa': pa})
    comps.sort(key=lambda c: _safe_float(c['re']), reverse=True)
    return comps


def _parse_attrs_params(text, preferred_band='nircam_f277w'):
    """Parse multi-band component_attributes.txt into component dicts.

    Uses *preferred_band* if present, otherwise the first band listed.
    Result is sorted by Re (descending).
    """
    bands = {}
    current_band = None
    current_comp = None
    for line in text.splitlines():
        bm = re.match(r'^\s*-\s*Band:\s*(.+?)\s*$', line)
        if bm:
            current_band = bm.group(1).strip()
            bands[current_band] = []
            current_comp = None
            continue
        cm = re.match(r'^\s*-\s*Component\s+(.+?):\s*$', line)
        if cm and current_band is not None:
            current_comp = {'name': cm.group(1).strip()}
            bands[current_band].append(current_comp)
            continue
        if current_comp is not None:
            for k, v in re.findall(r'(\w+):\s*(\S+)', line):
                current_comp[k] = _clean_num(v)
    if preferred_band in bands:
        chosen = bands[preferred_band]
    elif bands:
        chosen = next(iter(bands.values()))
    else:
        chosen = []
    result = []
    for comp in chosen:
        result.append({'name': comp.get('name', ''),
                       'x': comp.get('x', ''), 'y': comp.get('y', ''),
                       'mag': comp.get('mag', ''), 're': comp.get('re', ''),
                       'n': comp.get('n', ''), 'ba': comp.get('ba', ''),
                       'pa': comp.get('pa', '')})
    result.sort(key=lambda c: _safe_float(c['re']), reverse=True)
    return result


def _build_compare_cell(source, galaxy_id, fitting_type, best_turn, components,
                        base_path, db):
    """Build the render context for one (galaxy, source) comparison cell.

    Returns None when there is no best_turn, or the best_turn's round has no
    comparison PNG (so the template renders an empty cell). Mirrors
    sample_detail's fit.log / component_attributes.txt path logic and reuses
    the existing /image/<source>/<galaxy>/<timestamp_dir> route for the PNG.

    The log file is parsed into ``params`` — a list of component dicts
    (name, x, y, mag, re, n, ba, pa) sorted by Re descending — which the
    template renders in a unified cross-source comparison table.
    """
    if not best_turn:
        return None
    r = db.execute(
        'SELECT r.png_path, r.round_number, r.chi_squared_nu, r.bic '
        'FROM rounds r '
        'JOIN samples s ON r.sample_id = s.id '
        'WHERE s.source=? AND s.galaxy_id=? AND r.timestamp_dir=?',
        (source, galaxy_id, best_turn)).fetchone()
    if not r or not r['png_path']:
        return None

    round_data_dir = 'output' if fitting_type == 'multi-band' else 'archives'
    log_name = ('component_attributes.txt' if fitting_type == 'multi-band'
                else 'fit.log')
    log_path = os.path.join(base_path, galaxy_id, round_data_dir,
                            best_turn, log_name)
    params = []
    if os.path.isfile(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                raw_text = f.read()
            if fitting_type == 'single-band':
                params = _parse_fitlog_params(raw_text)
            else:
                params = _parse_attrs_params(raw_text)
        except OSError:
            pass

    return {
        'source': source,
        'galaxy_id': galaxy_id,
        'timestamp_dir': best_turn,
        'round_number': r['round_number'],
        'chi_squared_nu': r['chi_squared_nu'],
        'bic': r['bic'],
        'fitting_type': fitting_type,
        'params': params,
        'components': ', '.join(components) if components else '',
    }


# attributes to plot in the statistics section
_STAT_ATTRS = [('mag', 'Mag'), ('re', 'Re'), ('n', 'n')]


def _short_galaxy_label(gid):
    """Shorten a galaxy ID for plot labels.

    Full ID when it is all-digits; otherwise the last 5 characters.
    """
    if gid.isdigit():
        return gid
    return gid[-5:] if len(gid) > 5 else gid


def _collect_stats_pairs(columns, galaxy_ids):
    """Collect matched component pairs for cross-source statistics.

    A galaxy qualifies when both sources share the same analysis-report
    component list (e.g. {Disk, Bulge}) **and** the same count of parsed
    components.  The raw parsed names differ across file formats (fit.log
    yields GALFIT types like ``expdisk``/``sersic``; component_attributes.txt
    yields component names like ``disk``/``bulge``), so matching is done on
    the ``components`` field instead.  Both sides are already sorted by Re
    descending, so positional pairing (i-th with i-th) is meaningful.
    Returns a list of dicts: {galaxy, name, mag:(a,b), re:(a,b), n:(a,b)}.
    """
    pairs = []
    cells_a = columns[0]['cells']
    cells_b = columns[1]['cells']
    for gid in galaxy_ids:
        ca = cells_a.get(gid)
        cb = cells_b.get(gid)
        if not ca or not cb:
            continue
        pa = ca.get('params', [])
        pb = cb.get('params', [])
        if not pa or len(pa) != len(pb):
            continue
        # match on the analysis-report component set (consistent vocabulary),
        # not on raw parsed names which differ between file formats
        sa = _purify_component_set(
            c.strip() for c in ca.get('components', '').split(',') if c.strip())
        sb = _purify_component_set(
            c.strip() for c in cb.get('components', '').split(',') if c.strip())

        if not sa or sa != sb:
            continue
        for a, b in zip(pa, pb):
            row = {'galaxy': gid, 'name': a['name']}
            for key, _ in _STAT_ATTRS:
                row[key] = (_safe_float(a.get(key)),
                            _safe_float(b.get(key)))
            pairs.append(row)
    return pairs


def _make_comparison_scatter(pairs, attr_key, attr_label, label_a, label_b):
    """Generate a base64-encoded PNG scatter plot for one attribute.

    x = source-A value, y = source-B value, with a y=x reference line.
    Each point is labelled with a (shortened) galaxy ID.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import io
    import base64 as _b64

    xs = [p[attr_key][0] for p in pairs]
    ys = [p[attr_key][1] for p in pairs]

    fig, ax = plt.subplots(figsize=(5, 5))
    fig.patch.set_facecolor('#161b22')
    ax.set_facecolor('#0d1117')
    ax.tick_params(colors='#8b949e')
    for spine in ax.spines.values():
        spine.set_color('#30363d')
    ax.xaxis.label.set_color('#e6edf3')
    ax.yaxis.label.set_color('#e6edf3')
    ax.title.set_color('#e6edf3')

    if xs and ys:
        lo = min(min(xs), min(ys))
        hi = max(max(xs), max(ys))
        if lo == hi:
            lo, hi = lo - 1, hi + 1
        margin = (hi - lo) * 0.08
        lo, hi = lo - margin, hi + margin
        ax.plot([lo, hi], [lo, hi], '--', color='#8b949e',
                alpha=0.6, linewidth=1)
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)

    ax.scatter(xs, ys, c='#58a6ff', s=35, zorder=5,
               edgecolors='#0d1117', linewidths=0.5)
    for i, p in enumerate(pairs):
        ax.annotate(_short_galaxy_label(p['galaxy']),
                    (xs[i], ys[i]),
                    textcoords='offset points', xytext=(5, 4),
                    fontsize=6.5, color='#e6edf3', alpha=0.85)

    ax.set_xlabel('%s — %s' % (label_a, attr_label))
    ax.set_ylabel('%s — %s' % (label_b, attr_label))
    ax.set_aspect('equal')
    ax.grid(True, color='#30363d', alpha=0.25, linestyle='--')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return 'data:image/png;base64,' + _b64.b64encode(buf.getvalue()).decode()


@app.route('/compare/iou')
@login_required
def compare_iou():
    """AJAX: galaxy-set IoU across 2 sources.

    GET /compare/iou?sources=a,b,c -> {iou, inter_count, union_count, ok}
    Used by the nav modal to gate navigation: ok is true iff iou >= 0.5.
    """
    raw = (request.args.get('sources') or '').strip()
    labels = [s for s in raw.split(',') if s]
    if len(labels) < 2:
        return jsonify({'iou': 0.0, 'inter_count': 0, 'union_count': 0,
                        'ok': False, 'error': '需要至少 2 个数据源'})
    db = get_db()
    sets = {}
    for label in labels:
        rows = db.execute(
            'SELECT galaxy_id FROM samples WHERE source=? ORDER BY galaxy_id',
            (label,)).fetchall()
        sets[label] = {row['galaxy_id'] for row in rows}
    inter = set.intersection(*sets.values()) if sets else set()
    union = set.union(*sets.values()) if sets else set()
    iou = (len(inter) / len(union)) if union else 0.0
    return jsonify({'iou': round(iou, 4),
                    'inter_count': len(inter),
                    'union_count': len(union),
                    'ok': iou >= 0.5})


@app.route('/compare/<s1>/<s2>')
@login_required
def compare_sources(s1, s2):
    """Side-by-side best-fit comparison across 2 sources.

    Columns = the selected source labels (order preserved). Rows = the UNION
    of galaxy_ids across the sources (sorted). Each cell renders that source's
    AI-recommended (best_turn) round: the comparison PNG via
    /image/<source>/<galaxy>/<timestamp_dir>, plus the fit.log (single-band)
    or component_attributes.txt (multi-band). Missing (galaxy, source) pairs
    render an empty cell. Comparison results are computed per request and
    are NOT persisted.
    """
    sources = _get_sources_from_db()
    labels = [s1, s2]
    for label in labels:
        if label not in sources:
            abort(404)

    db = get_db()
    columns = []          # list of {label, fitting_type, cells:{gid:cell}}
    galaxy_sets = []
    for label in labels:
        base_path = sources[label]
        rows = db.execute(
            'SELECT id, galaxy_id, source, fitting_type, best_turn, best_components '
            'FROM samples WHERE source=? ORDER BY galaxy_id',
            (label,)).fetchall()
        cells = {}
        fitting_type = None
        for r in rows:
            ft = r['fitting_type'] or 'single-band'
            if fitting_type is None:
                fitting_type = ft
            best, comps = get_best_turn_meta(r, db)
            cells[r['galaxy_id']] = _build_compare_cell(
                label, r['galaxy_id'], ft, best, comps, base_path, db)
        columns.append({'label': label, 'fitting_type': fitting_type,
                        'cells': cells})
        galaxy_sets.append(set(cells.keys()))

    galaxy_ids = sorted(set.union(*galaxy_sets)) if galaxy_sets else []

    # --- collect matched-pair statistics ---
    stats_pairs = _collect_stats_pairs(columns, galaxy_ids)
    stats_plots = []
    if stats_pairs:
        for key, lbl in _STAT_ATTRS:
            try:
                plot = _make_comparison_scatter(
                    stats_pairs, key, lbl, labels[0], labels[1])
                stats_plots.append({'key': key, 'label': lbl, 'plot': plot})
            except Exception:
                pass
    stats_galaxies = sorted(set(p['galaxy'] for p in stats_pairs))

    return render_template('compare.html',
                           labels=labels, columns=columns,
                           galaxy_ids=galaxy_ids,
                           stats_plots=stats_plots,
                           stats_galaxies=stats_galaxies,
                           current_source=labels[0])


# --- Analysis Evaluation: Data Initialization ---

def init_analysis_data(app):
    """One-time: populate a_galaxies from analysis_results.json."""
    data_file = os.path.join(os.path.dirname(__file__), 'analysis_data', 'analysis_results.json')
    if not os.path.isfile(data_file):
        return
    with open(data_file, 'r', encoding='utf-8') as f:
        results = json.load(f)
    db = get_db()
    for entry in results:
        image_rel = entry['image_path'].lstrip('./')
        db.execute(
            'INSERT OR IGNORE INTO a_galaxies (galaxy_name, image_path, analysis_text, sort_order) '
            'VALUES (?, ?, ?, ?)',
            (entry['galaxy'], image_rel, entry['analysis'], entry['id'])
        )
    db.commit()


# --- Analysis Evaluation Routes ---

@app.route('/analysis/')
@login_required
def analysis_list():
    db = get_db()
    user_id = session['user_id']

    galaxies = db.execute('''
        SELECT g.id, g.galaxy_name, g.sort_order,
               e.image_desc_rating, e.residual_desc_rating, e.component_pred_rating,
               (SELECT COUNT(*) FROM a_evaluations WHERE galaxy_id = g.id) AS total_evals,
               (SELECT ROUND(AVG(component_pred_rating),1) FROM a_evaluations WHERE galaxy_id = g.id) AS avg_overall
        FROM a_galaxies g
        LEFT JOIN a_evaluations e ON e.galaxy_id = g.id AND e.user_id = ?
        ORDER BY g.sort_order
    ''', (user_id,)).fetchall()

    total = len(galaxies)
    evaluated = sum(1 for g in galaxies if g['component_pred_rating'] is not None)

    return render_template('analysis_list.html',
                           galaxies=galaxies, total=total, evaluated=evaluated)


@app.route('/analysis/eval/<galaxy_name>')
@login_required
def analysis_eval(galaxy_name):
    db = get_db()
    user_id = session['user_id']

    galaxy = db.execute('SELECT * FROM a_galaxies WHERE galaxy_name = ?',
                        (galaxy_name,)).fetchone()
    if not galaxy:
        abort(404)

    my_eval = db.execute('''
        SELECT image_desc_rating, residual_desc_rating, component_pred_rating, feedback,
               image_desc_feedback, residual_desc_feedback, component_pred_feedback
        FROM a_evaluations WHERE user_id = ? AND galaxy_id = ?
    ''', (user_id, galaxy['id'])).fetchone()

    # Previous / next navigation (by sort_order)
    prev_g = db.execute('''
        SELECT galaxy_name FROM a_galaxies
        WHERE sort_order < ? ORDER BY sort_order DESC LIMIT 1
    ''', (galaxy['sort_order'],)).fetchone()

    next_g = db.execute('''
        SELECT galaxy_name FROM a_galaxies
        WHERE sort_order > ? AND id NOT IN
            (SELECT galaxy_id FROM a_evaluations WHERE user_id = ?)
        ORDER BY sort_order LIMIT 1
    ''', (galaxy['sort_order'], user_id)).fetchone()

    # Render analysis text as Markdown HTML
    analysis_html = markdown.markdown(
        galaxy['analysis_text'],
        extensions=['tables', 'fenced_code', 'toc']
    )

    return render_template('analysis_eval.html',
                           galaxy=galaxy,
                           analysis_html=analysis_html,
                           my_eval=my_eval,
                           prev_galaxy=prev_g['galaxy_name'] if prev_g else None,
                           next_galaxy=next_g['galaxy_name'] if next_g else None)


@app.route('/analysis/eval/<galaxy_name>/rate', methods=['POST'])
@login_required
def analysis_rate(galaxy_name):
    db = get_db()
    user_id = session['user_id']

    galaxy = db.execute('SELECT id FROM a_galaxies WHERE galaxy_name = ?',
                        (galaxy_name,)).fetchone()
    if not galaxy:
        abort(404)

    image_desc = int(request.form.get('image_desc_rating', 0))
    residual_desc = int(request.form.get('residual_desc_rating', 0))
    component_pred = int(request.form.get('component_pred_rating', 0))
    feedback = request.form.get('feedback', '').strip()
    image_desc_fb = request.form.get('image_desc_feedback', '').strip()
    residual_desc_fb = request.form.get('residual_desc_feedback', '').strip()
    component_pred_fb = request.form.get('component_pred_feedback', '').strip()

    if not (1 <= image_desc <= 5 and 1 <= residual_desc <= 5 and 1 <= component_pred <= 5):
        return redirect(url_for('analysis_eval', galaxy_name=galaxy_name))

    db.execute('''
        INSERT INTO a_evaluations (user_id, galaxy_id, image_desc_rating, residual_desc_rating, component_pred_rating,
                                   feedback, image_desc_feedback, residual_desc_feedback, component_pred_feedback, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, galaxy_id) DO UPDATE SET
            image_desc_rating=excluded.image_desc_rating,
            residual_desc_rating=excluded.residual_desc_rating,
            component_pred_rating=excluded.component_pred_rating,
            feedback=excluded.feedback,
            image_desc_feedback=excluded.image_desc_feedback,
            residual_desc_feedback=excluded.residual_desc_feedback,
            component_pred_feedback=excluded.component_pred_feedback,
            updated_at=datetime('now')
    ''', (user_id, galaxy['id'], image_desc, residual_desc, component_pred, feedback, image_desc_fb, residual_desc_fb, component_pred_fb))
    db.commit()

    return redirect(url_for('analysis_eval', galaxy_name=galaxy_name))


@app.route('/analysis/image/<path:img_path>')
@login_required
def analysis_serve_image(img_path):
    base_dir = app.config['ANALYSIS_IMAGE_DIR']
    # Strip leading "filter_comp_q5/" if present (stored path includes it, mount does not)
    if img_path.startswith('filter_comp_q5/'):
        img_path = img_path[len('filter_comp_q5/'):]
    safe_path = os.path.normpath(os.path.join(base_dir, img_path))
    if not safe_path.startswith(os.path.normpath(base_dir)):
        abort(403)
    if not os.path.isfile(safe_path):
        abort(404)
    return send_file(safe_path, mimetype='image/png')


@app.route('/analysis/statistics')
@login_required
def analysis_statistics():
    db = get_db()

    users = db.execute('SELECT id, username FROM users ORDER BY username').fetchall()

    raw_galaxies = db.execute('''
        SELECT g.id, g.galaxy_name, g.sort_order,
               (SELECT COUNT(*) FROM a_evaluations WHERE galaxy_id = g.id) AS total_evals
        FROM a_galaxies g
        ORDER BY g.sort_order
    ''').fetchall()

    galaxies = []
    for g in raw_galaxies:
        evals = db.execute('''
            SELECT image_desc_rating, residual_desc_rating, component_pred_rating, feedback,
                   u.username
            FROM a_evaluations e JOIN users u ON e.user_id = u.id
            WHERE e.galaxy_id = ?
            ORDER BY u.username
        ''', (g['id'],)).fetchall()

        total = g['total_evals']
        if total > 0:
            avg_img = sum(e['image_desc_rating'] for e in evals) / total
            avg_res = sum(e['residual_desc_rating'] for e in evals) / total
            avg_comp = sum(e['component_pred_rating'] for e in evals) / total
            std_comp = stats_mod.stdev([e['component_pred_rating'] for e in evals]) if total > 1 else 0
        else:
            avg_img = avg_res = avg_comp = std_comp = 0

        galaxies.append({
            'galaxy_name': g['galaxy_name'],
            'sort_order': g['sort_order'],
            'total_evals': total,
            'avg_img': round(avg_img, 1),
            'avg_res': round(avg_res, 1),
            'avg_comp': round(avg_comp, 1),
            'std_comp': round(std_comp, 1),
            'evals': evals,
        })

    # Completion matrix
    matrix = {}
    for u in users:
        user_evals = db.execute(
            'SELECT g.galaxy_name FROM a_evaluations e '
            'JOIN a_galaxies g ON e.galaxy_id = g.id '
            'WHERE e.user_id = ?', (u['id'],)
        ).fetchall()
        matrix[u['username']] = [v['galaxy_name'] for v in user_evals]

    # Per-user summary
    user_summary = []
    for u in users:
        u_evals = db.execute('''
            SELECT image_desc_rating, residual_desc_rating, component_pred_rating
            FROM a_evaluations WHERE user_id = ?
        ''', (u['id'],)).fetchall()
        if u_evals:
            user_summary.append({
                'username': u['username'],
                'count': len(u_evals),
                'avg_overall': round(sum(e['component_pred_rating'] for e in u_evals) / len(u_evals), 1),
                'dist': {i: sum(1 for e in u_evals if e['component_pred_rating'] == i) for i in range(1, 6)},
            })

    return render_template('analysis_stats.html',
                           users=users, galaxies=galaxies,
                           matrix=matrix, user_summary=user_summary)


# --- visualRAG KB linkage (distillation + ingestion) ---

@app.route('/kb/health')
@login_required
def kb_health():
    """Proxy to the KB service /health (the browser can't reach the host service
    directly from inside the container). Drives the nav-bar link badge."""
    import kb_client
    h = kb_client.health()
    return jsonify({'enabled': kb_client.enabled(),
                    'connected': h is not None, 'health': h})


@app.route('/kb/review')
@login_required
def kb_review():
    db = get_db()

    # --- filters (GET) ---
    f_status = (request.args.get('status') or '').strip()
    f_library = (request.args.get('library') or '').strip()
    f_source = (request.args.get('source') or '').strip()
    f_q = (request.args.get('q') or '').strip()

    where, args = [], []
    if f_status in ('draft', 'committed'):
        where.append('status = ?'); args.append(f_status)
    if f_library in ('perfect', 'problem'):
        where.append('library = ?'); args.append(f_library)
    if f_source:
        where.append('source = ?'); args.append(f_source)
    if f_q:
        where.append('galaxy_id LIKE ?'); args.append(f'%{f_q}%')
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = db.execute(
        'SELECT * FROM kb_staging ' + where_sql +
        ' ORDER BY CASE status WHEN \'draft\' THEN 0 ELSE 1 END, '
        'source, galaxy_id, round_number',
        args).fetchall()

    items = []
    for row in rows:
        distilled = _kb_distilled(row)
        labels = _kb_labels(row)
        reasoning = distilled.get('reasoning', '') or ''
        # one-line preview: first non-empty line of the markdown reasoning
        diag = next((ln.strip() for ln in reasoning.split('\n') if ln.strip()), '')
        items.append({
            'row': row,
            'desc': distilled.get('image_description', ''),
            'reasoning': reasoning,
            'labels': labels,
            'diagnosis': (diag[:80] + '…') if len(diag) > 80 else diag,
        })

    # --- summary stats (unfiltered, for the dashboard header) ---
    all_rows = db.execute('SELECT status, library FROM kb_staging').fetchall()
    stats = {
        'draft': sum(1 for r in all_rows if r['status'] == 'draft'),
        'committed': sum(1 for r in all_rows if r['status'] == 'committed'),
        'perfect': sum(1 for r in all_rows if r['library'] == 'perfect'),
        'problem': sum(1 for r in all_rows if r['library'] == 'problem'),
    }

    # KB link health (best-effort; the badge already polls this too)
    kb_health = None
    try:
        import kb_client
        if kb_client.enabled():
            kb_health = kb_client.health()
    except Exception:  # noqa: BLE001
        kb_health = None

    # batch pre-ingest result summary (set by /admin/kb/preingest on redirect)
    batch_summary = None
    if request.args.get('batch_ok') is not None:
        try:
            batch_summary = {
                'ok': int(request.args.get('batch_ok') or 0),
                'skip': int(request.args.get('batch_skip') or 0),
                'fail': int(request.args.get('batch_fail') or 0),
            }
        except (TypeError, ValueError):
            batch_summary = None

    return render_template('kb_review.html', items=items, stats=stats,
                           kb_health=kb_health,
                           filters={'status': f_status, 'library': f_library,
                                    'source': f_source, 'q': f_q},
                           sources=_get_sources_from_db(),
                           taxonomy=TAXONOMY, batch_summary=batch_summary)


@app.route('/admin/kb/preingest/<source>', methods=['POST'])
@login_required
def kb_preingest(source):
    """Batch pre-ingest (path ①): distill a source's teaching cases into kb_staging
    drafts, following the skill's two-library selection (selection_criteria.md):

      * PERFECT library — an expert-voted acceptable fit (is_perfect=1) -> distill
        its best_round into the perfect library (V_orig morphology reference).
      * PROBLEM library — each VERIFIED-DIAGNOSIS round -> distill into the problem
        library (V_dual residual teaching). Round n qualifies iff it has a
        component_analysis report, a successor round n+1 exists, and n+1's χ²/ν
        dropped by >= KB_IMPROVED_MARGIN — i.e. the report's diagnosis actually
        helped. Reports that didn't help (wrong/noise diagnosis) are NOT distilled.

    Hard precondition: EVERY distilled round must carry a component_analysis report
    (the /distill transcription source) — gated via _round_can_distill, same check
    the per-round UI uses. Rounds without one are skipped (the resident manual-fill
    path covers them). Retry semantics via _stage_distill: successful/committed rows
    are skipped, failed rows are retried. Summary counts -> /kb/review query string."""
    if not session.get('is_admin'):
        abort(403)
    import kb_client
    container_path = _get_source_path(source)
    db = get_db()
    samples = db.execute(
        'SELECT id, galaxy_id FROM samples WHERE source = ? ORDER BY galaxy_id',
        (source,)).fetchall()
    n_ok = n_skip = n_fail = 0

    def tally(res):
        nonlocal n_ok, n_skip, n_fail
        if res == 'ok':
            n_ok += 1
        elif res == 'fail':
            n_fail += 1
        else:
            n_skip += 1

    for s in samples:
        sid, galaxy = s['id'], s['galaxy_id']
        rows = db.execute(
            "SELECT round_number, timestamp_dir, chi_squared_nu, round_status "
            "FROM rounds WHERE sample_id=? ORDER BY round_number", (sid,)).fetchall()
        by_num = {r['round_number']: r for r in rows}

        # --- PERFECT: expert-confirmed best round -> perfect library ---
        v = db.execute(
            "SELECT best_round FROM votes WHERE sample_id=? AND is_perfect=1 "
            "AND best_round IS NOT NULL ORDER BY updated_at DESC LIMIT 1",
            (sid,)).fetchone()
        if v:
            rnd = by_num.get(v['best_round'])
            if rnd and rnd['round_status'] == 'success' and \
                    _round_can_distill(source, galaxy, rnd['timestamp_dir']):
                distilled, err = kb_client.distill(
                    container_path, galaxy, rnd['timestamp_dir'], library='perfect')
                tally(_stage_distill(db, source, galaxy, rnd, 'perfect', distilled, err))

        # --- PROBLEM: each verified-diagnosis round -> problem library ---
        for r in rows:
            n = r['round_number']
            if r['round_status'] != 'success':
                continue
            if not _round_can_distill(source, galaxy, r['timestamp_dir']):
                continue
            nxt = by_num.get(n + 1)
            chi_n = r['chi_squared_nu']
            chi_n1 = nxt['chi_squared_nu'] if nxt else None
            if chi_n is None or chi_n1 is None or chi_n <= 0:
                continue
            if (chi_n - chi_n1) / chi_n < KB_IMPROVED_MARGIN:
                continue
            distilled, err = kb_client.distill(
                container_path, galaxy, r['timestamp_dir'], library='problem')
            tally(_stage_distill(db, source, galaxy, r, 'problem', distilled, err))

    # _stage_distill commits each row as it goes, so drafts survive an interruption
    # and are visible to other tabs (WAL) while this loop still runs.
    return redirect(url_for('kb_review', batch_ok=n_ok, batch_skip=n_skip, batch_fail=n_fail))


@app.route('/admin/kb/preingest/progress')
@login_required
def kb_preingest_progress():
    """Live progress for the batch overlay: how many drafts the source already has
    in kb_staging (climbs as the running batch commits each row) and how many
    samples the source holds. Read-only counts — login_required, no admin gate.
    ``staged`` is total drafts for the source (incl. prior runs), not run-scoped."""
    src = (request.args.get('src') or '').strip()
    if not src:
        return jsonify({'staged': 0, 'total': 0})
    db = get_db()
    total = db.execute(
        'SELECT COUNT(*) c FROM samples WHERE source=?', (src,)).fetchone()['c']
    staged = db.execute(
        "SELECT COUNT(*) c FROM kb_staging WHERE source=? AND status='draft'",
        (src,)).fetchone()['c']
    return jsonify({'staged': staged, 'total': total})


@app.route('/kb/stage/<source>/<galaxy_id>/<timestamp_dir>', methods=['POST'])
@login_required
def kb_stage_one(source, galaxy_id, timestamp_dir):
    """Resident single-round distill (path ③): distill one round and upsert a
    draft, then drop the expert onto the review page to confirm + commit."""
    import kb_client
    library = request.form.get('library', 'problem')
    container_path = _get_source_path(source)
    distilled, _err = kb_client.distill(container_path, galaxy_id, timestamp_dir, library=library)
    db = get_db()
    db.execute(
        'INSERT INTO kb_staging '
        '(sample_id, source, galaxy_id, timestamp_dir, library, distilled_json, status) '
        'VALUES (?,?,?,?,?,?, \'draft\') '
        'ON CONFLICT(source, galaxy_id, timestamp_dir) DO UPDATE SET '
        'library=excluded.library, distilled_json=excluded.distilled_json, '
        "status='draft', error=NULL, updated_at=datetime('now')",
        (f"{galaxy_id}_{timestamp_dir}", source, galaxy_id, timestamp_dir,
         library if distilled else None,
         json.dumps(distilled, ensure_ascii=False) if distilled else None))
    db.commit()
    return redirect(url_for('kb_review'))


@app.route('/kb/staging/<int:sid>/redistill', methods=['POST'])
@login_required
def kb_redistill(sid):
    """Re-distill one draft under a (possibly different) library."""
    import kb_client
    db = get_db()
    row = db.execute('SELECT * FROM kb_staging WHERE id = ?', (sid,)).fetchone()
    if not row:
        abort(404)
    library = request.form.get('library', row['library'] or 'problem')
    hint = request.form.get('hint', '').strip() or None
    distilled, err = kb_client.distill(_get_source_path(row['source']), row['galaxy_id'],
                                       row['timestamp_dir'], library=library, hint=hint)
    if distilled:
        db.execute(
            'UPDATE kb_staging SET library=?, distilled_json=?, error=NULL, '
            "updated_at=datetime('now') WHERE id=?",
            (library, json.dumps(distilled, ensure_ascii=False), sid))
    else:
        db.execute(
            'UPDATE kb_staging SET error=?, updated_at=datetime(\'now\') WHERE id=?',
            (err or '蒸馏失败（KB 服务或 VLM 错误）', sid))
    db.commit()
    return redirect(url_for('kb_review'))


@app.route('/kb/staging/<int:sid>/commit', methods=['POST'])
@login_required
def kb_commit(sid):
    """Commit one expert-confirmed draft to the live KB (path ②)."""
    if not session.get('is_admin'):
        abort(403)
    import kb_client
    db = get_db()
    row = db.execute('SELECT * FROM kb_staging WHERE id = ?', (sid,)).fetchone()
    if not row:
        abort(404)
    library = request.form.get('library', row['library'] or 'problem')
    distilled = _kb_distilled(row)

    # expert edits from the form (markdown strings) override the distilled JSON
    for field in ('image_description', 'reasoning'):
        raw = request.form.get(field, '')
        if raw:
            distilled[field] = raw
    try:
        final_labels = json.loads(request.form.get('final_labels', '[]'))
    except json.JSONDecodeError:
        final_labels = []

    container_path = _get_source_path(row['source'])
    payload = {
        'sample_id': row['sample_id'],
        'obj_id': row['galaxy_id'],
        'library': library,
        'final_labels': final_labels,
        'component_signature': _kb_signature(row),
        'image_description': distilled.get('image_description', ''),
        'reasoning': distilled.get('reasoning', ''),
        'archive_path': f"{container_path}/{row['galaxy_id']}/archives/{row['timestamp_dir']}",
    }
    res, err = kb_client.ingest(container_path, row['galaxy_id'], row['timestamp_dir'], payload)
    if res and res.get('sample_id'):
        db.execute(
            'UPDATE kb_staging SET status=\'committed\', library=?, committed_kb_id=?, '
            "error=NULL, updated_at=datetime('now') WHERE id=?",
            (library, res.get('sample_id'), sid))
        db.commit()
    else:
        db.execute(
            'UPDATE kb_staging SET error=?, updated_at=datetime(\'now\') WHERE id=?',
            (err or 'ingest failed (KB service error or duplicate sample_id)', sid))
        db.commit()
    return redirect(url_for('kb_review'))


@app.route('/kb/staging/<int:sid>/delete', methods=['POST'])
@login_required
def kb_delete_draft(sid):
    db = get_db()
    db.execute("DELETE FROM kb_staging WHERE id=? AND status='draft'", (sid,))
    db.commit()
    return redirect(url_for('kb_review'))


# --- KB AJAX panel routes (return HTML fragments for the rail/modal editor) ---
# Single render path: every action returns the re-rendered kb_panel for the
# (now-current) row, so the front end just swaps the fragment. The detail page
# and the kb_review modal both consume these.

@app.route('/kb/ajax/panel')
@login_required
def kb_ajax_panel():
    """Return the editor panel for a staging row. Resolves by `sid`, or by
    (source, galaxy_id, timestamp_dir) when no draft exists yet (state A)."""
    db = get_db()
    sid = request.args.get('sid')
    row = None
    if sid:
        row = db.execute('SELECT * FROM kb_staging WHERE id=?', (sid,)).fetchone()
    else:
        source = request.args.get('source', '')
        galaxy_id = request.args.get('galaxy_id', '')
        ts = request.args.get('timestamp_dir', '')
        if source and galaxy_id and ts:
            row = db.execute(
                'SELECT * FROM kb_staging WHERE source=? AND galaxy_id=? AND timestamp_dir=?',
                (source, galaxy_id, ts)).fetchone()
    if row:
        return _kb_render(row['source'], row['galaxy_id'], row['timestamp_dir'],
                          row['round_number'], row)
    # state A: no draft yet — render from the query params
    return _kb_render(request.args.get('source', ''),
                      request.args.get('galaxy_id', ''),
                      request.args.get('timestamp_dir', ''),
                      request.args.get('round_number') or '',
                      row=None)


@app.route('/kb/ajax/distill', methods=['POST'])
@login_required
def kb_ajax_distill():
    """Initial distill OR re-distill (path ③ compute). Upserts kb_staging and
    returns the re-rendered panel. On failure, keeps any prior draft intact."""
    import kb_client
    source = request.form.get('source', '')
    galaxy_id = request.form.get('galaxy_id', '')
    ts = request.form.get('timestamp_dir', '')
    round_number = request.form.get('round_number') or None
    library = request.form.get('library', 'problem')
    hint = (request.form.get('hint') or '').strip() or None
    container_path = _get_source_path(source)
    distilled, err = kb_client.distill(container_path, galaxy_id, ts,
                                       library=library, hint=hint)
    db = get_db()
    if distilled:
        db.execute(
            'INSERT INTO kb_staging '
            '(sample_id, source, galaxy_id, round_number, timestamp_dir, library, distilled_json, status) '
            'VALUES (?,?,?,?,?,?,?, \'draft\') '
            'ON CONFLICT(source, galaxy_id, timestamp_dir) DO UPDATE SET '
            'library=excluded.library, round_number=COALESCE(excluded.round_number, kb_staging.round_number), '
            'distilled_json=excluded.distilled_json, status=\'draft\', error=NULL, '
            "updated_at=datetime('now')",
            (f"{galaxy_id}_{ts}", source, galaxy_id, round_number, ts,
             library, json.dumps(distilled, ensure_ascii=False)))
        db.commit()
        row = db.execute(
            'SELECT * FROM kb_staging WHERE source=? AND galaxy_id=? AND timestamp_dir=?',
            (source, galaxy_id, ts)).fetchone()
        return _kb_render(source, galaxy_id, ts, round_number, row, flash='蒸馏完成')
    # failure: do NOT wipe an existing draft; mark the SPECIFIC error on it if present
    fail_msg = err or '蒸馏失败（KB 服务或 VLM 错误）'
    row = db.execute(
        'SELECT * FROM kb_staging WHERE source=? AND galaxy_id=? AND timestamp_dir=?',
        (source, galaxy_id, ts)).fetchone()
    if row:
        db.execute(
            "UPDATE kb_staging SET error=?, updated_at=datetime('now') WHERE id=?",
            (fail_msg, row['id']))
        db.commit()
        row = db.execute('SELECT * FROM kb_staging WHERE id=?', (row['id'],)).fetchone()
        return _kb_render(source, galaxy_id, ts, round_number, row)
    return _kb_render(source, galaxy_id, ts, round_number, row=None,
                      flash=fail_msg)


@app.route('/kb/ajax/preingest', methods=['POST'])
@login_required
def kb_ajax_preingest():
    """预入库 (manual): create a draft from the expert-FILLED content. No VLM,
    no live-KB write. The STATE A manual editor posts here only after the expert
    has written the fields — so we never pre-ingest an empty row by default.
    The draft then shows in /kb/review for admin 确认入库 (commit to live KB)."""
    source = request.form.get('source', '')
    galaxy_id = request.form.get('galaxy_id', '')
    ts = request.form.get('timestamp_dir', '')
    round_number = request.form.get('round_number') or None
    library = request.form.get('library', 'problem')
    distilled = {
        'image_description': request.form.get('image_description', ''),
        'reasoning': request.form.get('reasoning', ''),
    }
    labels = request.form.getlist('labels')
    signature = request.form.getlist('signature')
    db = get_db()
    db.execute(
        'INSERT INTO kb_staging '
        '(sample_id, source, galaxy_id, round_number, timestamp_dir, library, '
        'distilled_json, final_labels_json, signature_json, status) '
        'VALUES (?,?,?,?,?,?,?,?,?, \'draft\') '
        'ON CONFLICT(source, galaxy_id, timestamp_dir) DO UPDATE SET '
        'library=excluded.library, round_number=COALESCE(excluded.round_number, kb_staging.round_number), '
        'distilled_json=excluded.distilled_json, final_labels_json=excluded.final_labels_json, '
        'signature_json=excluded.signature_json, '
        'status=\'draft\', error=NULL, updated_at=datetime(\'now\')',
        (f"{galaxy_id}_{ts}", source, galaxy_id, round_number, ts, library,
         json.dumps(distilled, ensure_ascii=False),
         json.dumps(labels, ensure_ascii=False),
         json.dumps(signature, ensure_ascii=False)))
    db.commit()
    row = db.execute(
        'SELECT * FROM kb_staging WHERE source=? AND galaxy_id=? AND timestamp_dir=?',
        (source, galaxy_id, ts)).fetchone()
    return _kb_render(source, galaxy_id, ts, round_number, row,
                      flash='已预入库(草稿已创建),待管理员确认入库')


@app.route('/kb/ajax/save', methods=['POST'])
@login_required
def kb_ajax_save():
    """Save expert edits back to the draft (kb_staging only — no live KB)."""
    db = get_db()
    sid = request.form.get('sid')
    row = db.execute('SELECT * FROM kb_staging WHERE id=?', (sid,)).fetchone()
    if not row:
        return _kb_render('', '', '', '', row=None, flash='草稿不存在')
    distilled = {
        'image_description': request.form.get('image_description', ''),
        'reasoning': request.form.get('reasoning', ''),
    }
    labels = request.form.getlist('labels')
    signature = request.form.getlist('signature')
    library = request.form.get('library', row['library'] or 'problem')
    db.execute(
        "UPDATE kb_staging SET library=?, distilled_json=?, final_labels_json=?, "
        "signature_json=?, error=NULL, updated_at=datetime('now') WHERE id=?",
        (library, json.dumps(distilled, ensure_ascii=False),
         json.dumps(labels, ensure_ascii=False),
         json.dumps(signature, ensure_ascii=False), sid))
    db.commit()
    row = db.execute('SELECT * FROM kb_staging WHERE id=?', (sid,)).fetchone()
    return _kb_render(row['source'], row['galaxy_id'], row['timestamp_dir'],
                      row['round_number'], row, flash='已保存草稿')


@app.route('/kb/ajax/commit', methods=['POST'])
@login_required
def kb_ajax_commit():
    """Commit a draft to the live KB (path ②). Persists expert edits first, so
    a failed ingest still keeps the edited draft. Admin-only — the live-KB write
    is not easily reversible, so gate it like batch pre-ingest."""
    if not session.get('is_admin'):
        abort(403)
    import kb_client
    db = get_db()
    sid = request.form.get('sid')
    row = db.execute('SELECT * FROM kb_staging WHERE id=?', (sid,)).fetchone()
    if not row:
        return _kb_render('', '', '', '', row=None, flash='草稿不存在')
    distilled = {
        'image_description': request.form.get('image_description', ''),
        'reasoning': request.form.get('reasoning', ''),
    }
    labels = request.form.getlist('labels')
    signature = request.form.getlist('signature')
    library = request.form.get('library', row['library'] or 'problem')
    db.execute(
        "UPDATE kb_staging SET library=?, distilled_json=?, final_labels_json=?, "
        "signature_json=?, updated_at=datetime('now') WHERE id=?",
        (library, json.dumps(distilled, ensure_ascii=False),
         json.dumps(labels, ensure_ascii=False),
         json.dumps(signature, ensure_ascii=False), sid))
    db.commit()
    container_path = _get_source_path(row['source'])
    payload = {
        'sample_id': row['sample_id'],
        'obj_id': row['galaxy_id'],
        'library': library,
        'final_labels': labels,
        'component_signature': signature,
        'image_description': distilled['image_description'],
        'reasoning': distilled['reasoning'],
        'archive_path': f"{container_path}/{row['galaxy_id']}/archives/{row['timestamp_dir']}",
    }
    res, err = kb_client.ingest(container_path, row['galaxy_id'], row['timestamp_dir'], payload)
    flash = None
    if res and res.get('sample_id'):
        db.execute(
            "UPDATE kb_staging SET status='committed', library=?, committed_kb_id=?, "
            "error=NULL, updated_at=datetime('now') WHERE id=?",
            (library, res.get('sample_id'), sid))
        db.commit()
        flash = '已入库 live KB'
    else:
        db.execute(
            "UPDATE kb_staging SET error=?, updated_at=datetime('now') WHERE id=?",
            (err or '入库失败（KB 服务错误或 sample_id 重复）', sid))
        db.commit()
    row = db.execute('SELECT * FROM kb_staging WHERE id=?', (sid,)).fetchone()
    return _kb_render(row['source'], row['galaxy_id'], row['timestamp_dir'],
                      row['round_number'], row, flash=flash)


# --- live-KB management (browse / edit-metadata / delete committed entries) ---
#
# Distinct from the /kb/review + /kb/ajax/* draft pipeline above: this section
# operates on ALREADY-ingested entries via the service's /kb/entries RUD routes.
# Browse is open to any logged-in user; edit + delete are admin-only (the live
# KB write is not easily reversible — same gate as kb_commit). The browser
# reaches the view app, not the host KB service, so images + JSON are proxied.

KB_MANAGE_PER_PAGE = 50


def _safe_int(val, default=1):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


@app.route('/kb/manage')
@login_required
def kb_manage():
    """Browse the live KB: filter by library + id substring, paginate."""
    import kb_client
    library = request.args.get('library', '').strip() or None
    q = request.args.get('q', '').strip() or None
    page = max(1, _safe_int(request.args.get('page', '1')))
    limit = KB_MANAGE_PER_PAGE
    offset = (page - 1) * limit

    listing = kb_client.list_entries(library=library, q=q, limit=limit, offset=offset) or {}
    entries = listing.get('entries', [])
    total = listing.get('total', 0)
    pages = max(1, (total + limit - 1) // limit)

    kb_health = kb_client.health() if kb_client.enabled() else None
    return render_template(
        'kb_manage.html',
        entries=entries, total=total, page=page, pages=pages,
        filters={'library': library or '', 'q': q or ''},
        perfect_n=(kb_health or {}).get('perfect_size'),
        problem_n=(kb_health or {}).get('problem_size'),
        kb_health=kb_health, taxonomy=TAXONOMY,
        is_admin=bool(session.get('is_admin')))


@app.route('/kb/manage/image/<library>/<sample_id>')
@login_required
def kb_manage_image(library, sample_id):
    """Proxy an entry's comparison PNG from the KB service (the browser can't
    reach the host service directly). Mirrors the /kb/health proxy pattern."""
    import kb_client
    import requests
    url = kb_client.image_url(library, sample_id)
    if not url:
        abort(503)
    try:
        upstream = requests.get(url, timeout=30)
    except requests.RequestException:
        abort(503)
    if upstream.status_code != 200:
        abort(404)
    return Response(upstream.content,
                    mimetype=upstream.headers.get('Content-Type', 'image/png'))


@app.route('/kb/manage/entry/<library>/<sample_id>')
@login_required
def kb_manage_entry(library, sample_id):
    """Full record JSON for the edit modal (AJAX populates the form)."""
    import kb_client
    entry = kb_client.get_entry(library, sample_id)
    if entry is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(entry)


@app.route('/kb/query/<source>/<galaxy_id>/<timestamp_dir>')
@login_required
def kb_query(source, galaxy_id, timestamp_dir):
    """Retrieve the most-similar KB teaching cases for one round (AJAX).

    Packages the round's archive (the SAME zip /distill + /ingest use), POSTs it
    to the KB service /query, and returns a compact JSON for the round-title
    「相似检索」modal: ``{status, query, cases[...], warnings}``. Image and full
    details are NOT embedded here — the browser pulls them per-case via the
    /kb/manage/{image,entry} proxies, so the host KB service is never reached
    directly from inside the container.
    """
    import kb_client
    container_path = _get_source_path(source)
    resp, err = kb_client.query(container_path, galaxy_id, timestamp_dir,
                                top_k=1, strategy="both")
    if err or resp is None:
        return jsonify({'status': 'error', 'error': err or 'KB 服务不可用',
                        'query': {}, 'cases': [], 'warnings': []})

    def _trim(c, role):
        c = c or {}
        return {'role': role,
                'library': c.get('library') or '',
                'sample_id': c.get('sample_id') or '',
                'obj_id': c.get('obj_id') or '',
                'score': c.get('score')}

    # Collect non-empty cases in role order: baseline, positive, then hard_negs
    # (top_k=1 usually yields just baseline + positive; the UI caps at 3 anyway).
    cases = []
    if resp.get('baseline'):
        cases.append(_trim(resp['baseline'], 'baseline'))
    if resp.get('positive'):
        cases.append(_trim(resp['positive'], 'positive'))
    for c in (resp.get('hard_negatives') or []):
        cases.append(_trim(c, 'hard_negative'))
    cases = cases[:3]
    return jsonify({'status': resp.get('status', 'ok'),
                    'query': resp.get('query', {}),
                    'cases': cases,
                    'warnings': resp.get('warnings', [])})


@app.route('/kb/manage/update/<library>/<sample_id>', methods=['POST'])
@login_required
def kb_manage_update(library, sample_id):
    """Edit one committed entry's metadata (admin). Markdown-string teaching
    fields + GT labels + optional obj_id — mirrors kb_ajax_commit's payload shape."""
    if not session.get('is_admin'):
        abort(403)
    import kb_client
    patch = {
        'final_labels': request.form.getlist('labels'),
        'component_signature': request.form.getlist('signature'),
        'image_description': request.form.get('image_description', ''),
        'reasoning': request.form.get('reasoning', ''),
    }
    obj_id = request.form.get('obj_id', '').strip()
    if obj_id:
        patch['obj_id'] = obj_id
    kb_client.update_entry(library, sample_id, patch)
    return _kb_manage_back()


@app.route('/kb/manage/delete/<library>/<sample_id>', methods=['POST'])
@login_required
def kb_manage_delete(library, sample_id):
    """Remove one committed entry from the live KB (admin)."""
    if not session.get('is_admin'):
        abort(403)
    import kb_client
    kb_client.delete_entry(library, sample_id)
    return _kb_manage_back()


def _kb_manage_back():
    """Redirect back to /kb/manage preserving the current filter/pagination."""
    return redirect(url_for('kb_manage',
                            library=request.form.get('ret_library', ''),
                            q=request.form.get('ret_q', ''),
                            page=request.form.get('ret_page', '1')))


# --- Admin ---

ADMIN_PASSWORD = '123456'


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin/login', methods=['GET', 'POST'])
@login_required
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_sources'))
        return render_template('admin_login.html', error='密码错误')
    return render_template('admin_login.html')


def _admin_sources_context():
    """Build the shared context for the /admin/sources page.

    Used by both the GET handler and the label-set import error path so an
    import error can re-render the page directly (preserving the user's
    typed JSON in the form) instead of redirecting and losing the input.
    """
    db = get_db()
    source_list = db.execute(
        'SELECT s.label, s.container_path, s.parent_dir, s.description, s.created_at, '
        '(SELECT COUNT(*) FROM samples WHERE source = s.label) AS galaxy_count '
        'FROM sources s ORDER BY s.sort_order, s.id'
    ).fetchall()

    parent_dirs = app.config.get('GALFIT_PARENT_DIRS', {})
    registered_paths = {r['container_path'] for r in source_list}

    available = {}
    for pname, ppath in parent_dirs.items():
        dirs = []
        if os.path.isdir(ppath):
            for entry in sorted(os.listdir(ppath)):
                full = os.path.join(ppath, entry)
                if os.path.isdir(full):
                    has_data = any(
                        os.path.isdir(os.path.join(full, e, 'archives')) or
                        os.path.isdir(os.path.join(full, e, 'output'))
                        for e in os.listdir(full)
                        if os.path.isdir(os.path.join(full, e))
                    )
                    dirs.append({
                        'name': entry,
                        'path': full,
                        'registered': full in registered_paths,
                        'has_data': has_data,
                    })
        available[pname] = {'path': ppath, 'dirs': dirs}

    label_set_list = db.execute(
        'SELECT id, name, description, galaxy_count, created_at '
        'FROM label_sets ORDER BY id'
    ).fetchall()

    return dict(source_list=source_list,
                available=available,
                parent_dirs=parent_dirs,
                label_set_list=label_set_list)


@app.route('/admin/sources')
@login_required
def admin_sources():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    ctx = _admin_sources_context()
    ctx['labelset_error'] = request.args.get('labelset_error', '')
    ctx['pending_json_text'] = ''
    ctx['pending_description'] = ''
    return render_template('admin_sources.html', **ctx)


@app.route('/admin/sources/add', methods=['POST'])
@login_required
def admin_source_add():
    if not session.get('is_admin'):
        abort(403)
    label = request.form.get('label', '').strip()
    parent_name = request.form.get('parent_name', '').strip()
    subdirectory = request.form.get('subdirectory', '').strip()
    description = request.form.get('description', '').strip()

    if not label or not parent_name or not subdirectory:
        abort(400)

    parent_dirs = app.config.get('GALFIT_PARENT_DIRS', {})
    if parent_name not in parent_dirs:
        abort(400)

    parent_path = parent_dirs[parent_name]
    container_path = os.path.join(parent_path, subdirectory)

    # Prevent path traversal
    real_parent = os.path.realpath(parent_path)
    real_path = os.path.realpath(container_path)
    if not real_path.startswith(real_parent + '/') or not os.path.isdir(real_path):
        abort(400)

    db = get_db()
    db.execute(
        'INSERT INTO sources (label, container_path, parent_dir, description) VALUES (?, ?, ?, ?)',
        (label, container_path, parent_name, description)
    )
    db.commit()

    scan_galaxies(label, container_path, db)

    return redirect(url_for('admin_sources'))


@app.route('/admin/sources/<label>/remove', methods=['POST'])
@login_required
def admin_source_remove(label):
    if not session.get('is_admin'):
        abort(403)
    db = get_db()
    source = db.execute('SELECT * FROM sources WHERE label = ?', (label,)).fetchone()
    if not source:
        abort(404)

    sample_ids = [r['id'] for r in db.execute(
        'SELECT id FROM samples WHERE source = ?', (label,)
    ).fetchall()]

    if sample_ids:
        placeholders = ','.join('?' * len(sample_ids))
        db.execute(f'DELETE FROM votes WHERE sample_id IN ({placeholders})', sample_ids)
        db.execute(f'DELETE FROM rounds WHERE sample_id IN ({placeholders})', sample_ids)
    db.execute('DELETE FROM samples WHERE source = ?', (label,))
    db.execute('DELETE FROM sources WHERE label = ?', (label,))
    db.commit()

    return redirect(url_for('admin_sources'))


@app.route('/admin/sources/<label>/update', methods=['POST'])
@login_required
def admin_source_update(label):
    if not session.get('is_admin'):
        abort(403)
    data = request.get_json(force=True)
    description = data.get('description', '').strip()
    db = get_db()
    db.execute('UPDATE sources SET description = ? WHERE label = ?', (description, label))
    db.commit()
    return jsonify({'ok': True})


@app.route('/admin/sources/<label>/rescan', methods=['POST'])
@login_required
def admin_source_rescan(label):
    if not session.get('is_admin'):
        abort(403)
    db = get_db()
    source = db.execute('SELECT * FROM sources WHERE label = ?', (label,)).fetchone()
    if not source:
        abort(404)
    scan_galaxies(label, source['container_path'], db)
    return redirect(url_for('admin_sources'))


@app.route('/admin/sources/reorder', methods=['POST'])
@login_required
def admin_source_reorder():
    if not session.get('is_admin'):
        abort(403)
    order = request.get_json().get('order', [])
    db = get_db()
    for i, label in enumerate(order):
        db.execute('UPDATE sources SET sort_order = ? WHERE label = ?', (i, label))
    db.commit()
    return {'ok': True}


@app.route('/admin/rescan', methods=['POST'])
@login_required
def rescan():
    if not session.get('is_admin'):
        abort(403)
    db = get_db()
    for source in db.execute('SELECT label, container_path FROM sources').fetchall():
        scan_galaxies(source['label'], source['container_path'], db)
    return redirect(url_for('admin_sources'))


# --- Fitting-scoring label set management (拟合评分 标签集) ---

@app.route('/admin/labelsets/add', methods=['POST'])
@login_required
def admin_labelset_add():
    """Import a label set from a pasted JSON string.

    Reads ``json_text`` (raw JSON) + optional ``description``. The JSON must
    carry a ``dataset`` name and a ``galaxies`` list of {galaxy_id, components}.
    A ``dataset`` name that already exists is REJECTED (not overwritten) — the
    user must delete the existing set first. On any error the page is re-
    rendered directly so the pasted JSON stays in the textarea.
    """
    if not session.get('is_admin'):
        abort(403)
    json_text = request.form.get('json_text', '').strip()
    description = request.form.get('description', '').strip()

    def fail(message):
        ctx = _admin_sources_context()
        ctx['labelset_error'] = message
        # preserve the user's input across the error render
        ctx['pending_json_text'] = json_text
        ctx['pending_description'] = description
        return render_template('admin_sources.html', **ctx)

    if not json_text:
        return fail('请粘贴标签集 JSON 内容。')
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        return fail('解析 JSON 失败: %s' % e)

    name = (data.get('dataset') or '').strip()
    galaxies = data.get('galaxies')
    if not name:
        return fail('JSON 缺少 dataset 字段。')
    if not isinstance(galaxies, list):
        return fail('JSON 的 galaxies 字段必须是列表。')

    db = get_db()
    existing = db.execute('SELECT id FROM label_sets WHERE name=?', (name,)).fetchone()
    if existing:
        return fail('dataset「%s」已存在，请先删除同名标签集或修改 JSON 里的 dataset。' % name)

    cur = db.execute(
        'INSERT INTO label_sets (name, description) VALUES (?, ?)',
        (name, description))
    lid = cur.lastrowid

    count = 0
    for g in galaxies:
        if not isinstance(g, dict):
            continue
        gid = str(g.get('galaxy_id', '')).strip()
        comps = g.get('components', [])
        if not gid or not isinstance(comps, list):
            continue
        db.execute(
            'INSERT OR REPLACE INTO label_galaxies (label_set_id, galaxy_id, components_json) '
            'VALUES (?, ?, ?)',
            (lid, gid, json.dumps(comps)))
        count += 1

    db.execute('UPDATE label_sets SET galaxy_count=? WHERE id=?', (count, lid))
    db.commit()
    return redirect(url_for('admin_sources'))


@app.route('/admin/labelsets/<int:lid>/remove', methods=['POST'])
@login_required
def admin_labelset_remove(lid):
    if not session.get('is_admin'):
        abort(403)
    db = get_db()
    ls = db.execute('SELECT id FROM label_sets WHERE id=?', (lid,)).fetchone()
    if not ls:
        abort(404)
    db.execute('DELETE FROM label_galaxies WHERE label_set_id=?', (lid,))
    db.execute('DELETE FROM label_sets WHERE id=?', (lid,))
    db.commit()
    return redirect(url_for('admin_sources'))


@app.route('/admin/labelsets/<int:lid>/update', methods=['POST'])
@login_required
def admin_labelset_update(lid):
    if not session.get('is_admin'):
        abort(403)
    data = request.get_json(force=True)
    description = data.get('description', '').strip()
    db = get_db()
    db.execute('UPDATE label_sets SET description=? WHERE id=?', (description, lid))
    db.commit()
    return jsonify({'ok': True})


@app.route('/admin/labelsets/<int:lid>/view')
@login_required
def admin_labelset_view(lid):
    """Return a label set as a formatted JSON string (one galaxy per line).

    Response: {name, count, text} where ``text`` is valid JSON with the
    ``galaxies`` array laid out so each galaxy_id + components sits on its
    own line, for easy on-screen reading.
    """
    if not session.get('is_admin'):
        abort(403)
    db = get_db()
    ls = db.execute('SELECT name FROM label_sets WHERE id=?', (lid,)).fetchone()
    if not ls:
        abort(404)
    rows = db.execute(
        'SELECT galaxy_id, components_json FROM label_galaxies '
        'WHERE label_set_id=? ORDER BY id', (lid,)).fetchall()

    galaxy_lines = []
    for r in rows:
        try:
            comps = json.loads(r['components_json']) if r['components_json'] else []
        except (ValueError, TypeError):
            comps = []
        galaxy_lines.append(
            '    {"galaxy_id": %s, "components": %s}' % (
                json.dumps(r['galaxy_id'], ensure_ascii=False),
                json.dumps(comps, ensure_ascii=False)))
    text = '{\n  "dataset": %s,\n  "galaxies": [\n%s\n  ]\n}' % (
        json.dumps(ls['name'], ensure_ascii=False),
        ',\n'.join(galaxy_lines))
    return jsonify({'name': ls['name'], 'count': len(rows), 'text': text})


# --- Fitting scoring (拟合评分) ---

@app.route('/score/<int:labelset_id>/<source>')
@login_required
def score_page(labelset_id, source):
    """Score a data source's best-fit components against a label set.

    Iterates ALL galaxies fitted under ``source``: each is bucketed into
    correct / wrong (when a label exists for that galaxy_id) or missing-label
    (no label -> shown separately, excluded from the accuracy denominator).
    Component comparison reuses ``_purify_component_set`` so the scoring
    vocabulary matches the compare-page statistics exactly.
    """
    sources = _get_sources_from_db()
    if source not in sources:
        abort(404)
    db = get_db()
    ls = db.execute('SELECT * FROM label_sets WHERE id=?', (labelset_id,)).fetchone()
    if not ls:
        abort(404)

    # Build galaxy_id -> purified label-component set
    label_rows = db.execute(
        'SELECT galaxy_id, components_json FROM label_galaxies WHERE label_set_id=?',
        (labelset_id,)).fetchall()
    label_index = {}
    for r in label_rows:
        try:
            comps = json.loads(r['components_json']) if r['components_json'] else []
        except (ValueError, TypeError):
            comps = []
        label_index[r['galaxy_id']] = _purify_component_set(comps)

    sample_rows = db.execute(
        'SELECT id, galaxy_id, source, fitting_type, best_turn, best_components '
        'FROM samples WHERE source=? ORDER BY galaxy_id', (source,)).fetchall()

    correct, wrong, missing_label = [], [], []
    for r in sample_rows:
        _bt, comps = get_best_turn_meta(r, db)
        fitted = _purify_component_set(comps)
        gid = r['galaxy_id']
        if gid not in label_index:
            missing_label.append({'galaxy_id': gid, 'fitted': sorted(fitted)})
            continue
        expected = label_index[gid]
        entry = {
            'galaxy_id': gid,
            'fitted': sorted(fitted),
            'expected': sorted(expected),
        }
        if fitted == expected:
            correct.append(entry)
        else:
            wrong.append(entry)

    denom = len(correct) + len(wrong)
    accuracy = (len(correct) / denom) if denom else 0.0

    return render_template('score.html',
                           labelset=ls, source=source,
                           correct=correct, wrong=wrong,
                           missing_label=missing_label,
                           accuracy=accuracy,
                           total_evaluated=denom,
                           total_source=len(sample_rows),
                           current_source=source)


# --- App lifecycle ---

app.teardown_appcontext(close_db)


if __name__ == '__main__':
    init_db(app)
    with app.app_context():
        db = get_db()
        for source in db.execute('SELECT label, container_path FROM sources').fetchall():
            scan_galaxies(source['label'], source['container_path'], db)
        init_analysis_data(app)
    app.run(debug=True, host='0.0.0.0', port=35092)
