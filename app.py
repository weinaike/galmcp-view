"""Galaxy Fitting Result Voting Web Application."""

import os
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

        # Find fit log
        log_name = 'run.log' if round_fitting_type == 'multi-band' else 'fit.log'
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

    return render_template('sample_detail.html',
                           source=source,
                           current_source=source,
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
