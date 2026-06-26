"""Galaxy Fitting Result Voting Web Application."""

import os
import glob
import json
import statistics as stats_mod
from collections import OrderedDict
from functools import wraps
import markdown
from flask import (Flask, render_template, request, redirect, url_for,
                   session, send_file, abort, Response, jsonify)
from config import Config
from database import get_db, close_db, init_db
from scanner import scan_galaxies


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
    return {'sources': _get_sources_from_db()}


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

    galaxy_dir = os.path.join(base_path, galaxy_id)
    try:
        candidates = [
            f for f in sorted(os.listdir(galaxy_dir))
            if 'analysis_report' in f and f.endswith('.md')
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return os.path.join(galaxy_dir, candidates[0])


def _find_working_note_path(galaxy_id, base_path):
    galaxy_dir = os.path.join(base_path, galaxy_id)
    path = os.path.join(galaxy_dir, 'working_note.md')
    return path if os.path.isfile(path) else None


def _parse_best_turn(galaxy_id, base_path):
    """Extract best_turn timestamp_dir from analysis_report.md JSON block."""
    import re
    report_path = _find_analysis_report_path(galaxy_id, base_path)
    if not report_path:
        return None
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError:
        return None
    m = re.search(r'"best_turn"\s*:\s*"([^"]+)"', content)
    return m.group(1) if m else None


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
                           best_turn=_parse_best_turn(galaxy_id, base_path),
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


@app.route('/admin/sources')
@login_required
def admin_sources():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
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

    return render_template('admin_sources.html',
                           source_list=source_list,
                           available=available,
                           parent_dirs=parent_dirs)


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


# --- App lifecycle ---

app.teardown_appcontext(close_db)


if __name__ == '__main__':
    init_db(app)
    with app.app_context():
        db = get_db()
        for source in db.execute('SELECT label, container_path FROM sources').fetchall():
            scan_galaxies(source['label'], source['container_path'], db)
        init_analysis_data(app)
    app.run(debug=True, host='0.0.0.0', port=35091)
