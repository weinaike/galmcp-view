"""Galaxy Fitting Result Voting Web Application."""

import os
import json
import statistics as stats_mod
from functools import wraps
import markdown
from flask import (Flask, render_template, request, redirect, url_for,
                   session, send_file, abort, Response)
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
        return redirect(url_for('sample_list'))
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


# --- Sample list ---

@app.route('/voting/')
@login_required
def sample_list():
    db = get_db()
    user_id = session['user_id']

    samples = db.execute('''
        SELECT s.galaxy_id, s.num_rounds,
               v.is_perfect, v.best_round, v.reason, v.comments,
               (SELECT COUNT(*) FROM votes WHERE sample_id = s.id) AS total_votes
        FROM samples s
        LEFT JOIN votes v ON v.sample_id = s.id AND v.user_id = ?
        ORDER BY s.galaxy_id
    ''', (user_id,)).fetchall()

    total = len(samples)
    evaluated = sum(1 for s in samples if s['is_perfect'] is not None)

    return render_template('sample_list.html',
                           samples=samples, total=total, evaluated=evaluated)


# --- Sample detail ---

@app.route('/sample/<galaxy_id>')
@login_required
def sample_detail(galaxy_id):
    db = get_db()
    user_id = session['user_id']

    sample = db.execute('SELECT * FROM samples WHERE galaxy_id = ?',
                        (galaxy_id,)).fetchone()
    if not sample:
        abort(404)

    # Check if analysis report exists
    base_path = app.config['GALFIT_BASE_PATH']
    report_path = os.path.join(base_path, galaxy_id, f'analysis_report_{galaxy_id}.md')
    has_analysis_report = os.path.isfile(report_path)

    rounds = db.execute('''
        SELECT id, round_number, timestamp_dir, png_path, chi_squared_nu, components_json, summary_path
        FROM rounds
        WHERE sample_id = ?
        ORDER BY round_number
    ''', (sample['id'],)).fetchall()

    rounds_data = []
    for r in rounds:
        fit_log_path = os.path.join(base_path, galaxy_id, 'archives', r['timestamp_dir'], 'fit.log')
        fit_log_content = ''
        if os.path.isfile(fit_log_path):
            try:
                with open(fit_log_path, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                # 跳过第 2-6 行（索引 1-5）：空行 + 文件路径信息
                fit_log_content = ''.join(lines[:1] + lines[6:])
            except OSError:
                pass
        # Check for component analysis file (*_component_analysis.md)
        archive_dir = os.path.join(base_path, galaxy_id, 'archives', r['timestamp_dir'])
        comp_analysis_file = None
        try:
            comp_analysis_file = next(
                (f for f in os.listdir(archive_dir) if f.endswith('_component_analysis.md')),
                None
            )
        except OSError:
            pass

        rounds_data.append({
            'round_number': r['round_number'],
            'timestamp_dir': r['timestamp_dir'],
            'has_png': r['png_path'] is not None,
            'has_summary': r['summary_path'] is not None,
            'has_comp_analysis': comp_analysis_file is not None,
            'comp_analysis_path': os.path.join(archive_dir, comp_analysis_file) if comp_analysis_file else '',
            'chi_squared_nu': r['chi_squared_nu'],
            'fit_log': fit_log_content,
        })

    my_vote = db.execute('''
        SELECT is_perfect, best_round, reason, comments
        FROM votes WHERE user_id = ? AND sample_id = ?
    ''', (user_id, sample['id'])).fetchone()

    prev_sample = db.execute('''
        SELECT s.galaxy_id FROM samples s
        WHERE s.galaxy_id < ?
        ORDER BY s.galaxy_id DESC LIMIT 1
    ''', (galaxy_id,)).fetchone()

    next_sample = db.execute('''
        SELECT s.galaxy_id FROM samples s
        WHERE s.galaxy_id > ? AND s.id NOT IN
            (SELECT sample_id FROM votes WHERE user_id = ?)
        ORDER BY s.galaxy_id LIMIT 1
    ''', (galaxy_id, user_id)).fetchone()

    return render_template('sample_detail.html',
                           galaxy_id=galaxy_id,
                           sample=sample,
                           rounds=rounds_data,
                           my_vote=my_vote,
                           has_analysis_report=has_analysis_report,
                           s4g_components=_get_s4g_components(galaxy_id),
                           prev_sample=prev_sample['galaxy_id'] if prev_sample else None,
                           next_sample=next_sample['galaxy_id'] if next_sample else None)


@app.route('/sample/<galaxy_id>/vote', methods=['POST'])
@login_required
def submit_vote(galaxy_id):
    db = get_db()
    user_id = session['user_id']

    sample = db.execute('SELECT id FROM samples WHERE galaxy_id = ?',
                        (galaxy_id,)).fetchone()
    if not sample:
        abort(404)

    is_perfect = int(request.form.get('is_perfect', 0))
    best_round = request.form.get('best_round', '').strip()
    reason = request.form.get('reason', '').strip()
    comments = request.form.get('comments', '').strip()

    if is_perfect == 1:
        if not best_round:
            return redirect(url_for('sample_detail', galaxy_id=galaxy_id))
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

    return redirect(url_for('sample_detail', galaxy_id=galaxy_id))


# --- Image serving ---

@app.route('/image/<galaxy_id>/<timestamp_dir>')
@login_required
def serve_image(galaxy_id, timestamp_dir):
    db = get_db()
    row = db.execute('''
        SELECT r.png_path FROM rounds r
        JOIN samples s ON r.sample_id = s.id
        WHERE s.galaxy_id = ? AND r.timestamp_dir = ?
    ''', (galaxy_id, timestamp_dir)).fetchone()

    if not row or not row['png_path']:
        abort(404)

    png_path = row['png_path']
    if not os.path.isfile(png_path):
        abort(404)

    return send_file(png_path, mimetype='image/png')


# --- Analysis report serving ---

@app.route('/analysis-report/<galaxy_id>')
@login_required
def serve_analysis_report(galaxy_id):
    report_path = os.path.join(app.config['GALFIT_BASE_PATH'],
                               galaxy_id, f'analysis_report_{galaxy_id}.md')
    if not os.path.isfile(report_path):
        abort(404)
    with open(report_path, 'r', encoding='utf-8') as f:
        content = f.read()
    html = markdown.markdown(content, extensions=['tables', 'fenced_code', 'toc'])
    return Response(html, mimetype='text/html; charset=utf-8')


# --- Summary log serving ---

@app.route('/summary/<galaxy_id>/<timestamp_dir>')
@login_required
def serve_summary(galaxy_id, timestamp_dir):
    db = get_db()
    row = db.execute('''
        SELECT r.summary_path, r.timestamp_dir FROM rounds r
        JOIN samples s ON r.sample_id = s.id
        WHERE s.galaxy_id = ? AND r.timestamp_dir = ?
    ''', (galaxy_id, timestamp_dir)).fetchone()

    if not row:
        abort(404)

    summary_path = row['summary_path']
    if not summary_path or not os.path.isfile(summary_path):
        abort(404)

    with open(summary_path, 'r', encoding='utf-8') as f:
        content = f.read()

    return Response(content, mimetype='text/plain; charset=utf-8')


# --- Component analysis serving ---

@app.route('/component-analysis/<galaxy_id>/<timestamp_dir>')
@login_required
def serve_component_analysis(galaxy_id, timestamp_dir):
    archive_dir = os.path.join(app.config['GALFIT_BASE_PATH'],
                               galaxy_id, 'archives', timestamp_dir)
    try:
        filename = next(
            (f for f in os.listdir(archive_dir) if f.endswith('_component_analysis.md')),
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
@login_required
def statistics():
    db = get_db()

    users = db.execute('SELECT id, username FROM users ORDER BY username').fetchall()

    raw_samples = db.execute('''
        SELECT s.id, s.galaxy_id, s.num_rounds,
               (SELECT COUNT(*) FROM votes WHERE sample_id = s.id) AS total_votes,
               (SELECT COUNT(*) FROM votes WHERE sample_id = s.id AND is_perfect = 1) AS perfect_count
        FROM samples s
        ORDER BY s.galaxy_id
    ''').fetchall()

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
            'WHERE v.user_id = ?', (u['id'],)
        ).fetchall()
        matrix[u['username']] = [v['galaxy_id'] for v in user_votes]

    return render_template('statistics.html',
                           users=users, samples=samples,
                           vote_data=vote_data, matrix=matrix)


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

@app.route('/')
@login_required
def analysis_list():
    db = get_db()
    user_id = session['user_id']

    galaxies = db.execute('''
        SELECT g.id, g.galaxy_name, g.sort_order,
               e.residual_desc_rating, e.reasoning_rating, e.overall_rating,
               (SELECT COUNT(*) FROM a_evaluations WHERE galaxy_id = g.id) AS total_evals,
               (SELECT ROUND(AVG(overall_rating),1) FROM a_evaluations WHERE galaxy_id = g.id) AS avg_overall
        FROM a_galaxies g
        LEFT JOIN a_evaluations e ON e.galaxy_id = g.id AND e.user_id = ?
        ORDER BY g.sort_order
    ''', (user_id,)).fetchall()

    total = len(galaxies)
    evaluated = sum(1 for g in galaxies if g['overall_rating'] is not None)

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
        SELECT residual_desc_rating, reasoning_rating, overall_rating, feedback
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

    residual = int(request.form.get('residual_desc_rating', 0))
    reasoning = int(request.form.get('reasoning_rating', 0))
    overall = int(request.form.get('overall_rating', 0))
    feedback = request.form.get('feedback', '').strip()

    if not (1 <= residual <= 5 and 1 <= reasoning <= 5 and 1 <= overall <= 5):
        return redirect(url_for('analysis_eval', galaxy_name=galaxy_name))

    db.execute('''
        INSERT INTO a_evaluations (user_id, galaxy_id, residual_desc_rating, reasoning_rating, overall_rating, feedback, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, galaxy_id) DO UPDATE SET
            residual_desc_rating=excluded.residual_desc_rating,
            reasoning_rating=excluded.reasoning_rating,
            overall_rating=excluded.overall_rating,
            feedback=excluded.feedback,
            updated_at=datetime('now')
    ''', (user_id, galaxy['id'], residual, reasoning, overall, feedback))
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
            SELECT residual_desc_rating, reasoning_rating, overall_rating, feedback,
                   u.username
            FROM a_evaluations e JOIN users u ON e.user_id = u.id
            WHERE e.galaxy_id = ?
            ORDER BY u.username
        ''', (g['id'],)).fetchall()

        total = g['total_evals']
        if total > 0:
            avg_res = sum(e['residual_desc_rating'] for e in evals) / total
            avg_rea = sum(e['reasoning_rating'] for e in evals) / total
            avg_ovr = sum(e['overall_rating'] for e in evals) / total
            std_ovr = stats_mod.stdev([e['overall_rating'] for e in evals]) if total > 1 else 0
        else:
            avg_res = avg_rea = avg_ovr = std_ovr = 0

        galaxies.append({
            'galaxy_name': g['galaxy_name'],
            'sort_order': g['sort_order'],
            'total_evals': total,
            'avg_res': round(avg_res, 1),
            'avg_rea': round(avg_rea, 1),
            'avg_ovr': round(avg_ovr, 1),
            'std_ovr': round(std_ovr, 1),
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
            SELECT residual_desc_rating, reasoning_rating, overall_rating
            FROM a_evaluations WHERE user_id = ?
        ''', (u['id'],)).fetchall()
        if u_evals:
            user_summary.append({
                'username': u['username'],
                'count': len(u_evals),
                'avg_overall': round(sum(e['overall_rating'] for e in u_evals) / len(u_evals), 1),
                'dist': {i: sum(1 for e in u_evals if e['overall_rating'] == i) for i in range(1, 6)},
            })

    return render_template('analysis_stats.html',
                           users=users, galaxies=galaxies,
                           matrix=matrix, user_summary=user_summary)


# --- Admin ---

@app.route('/admin/rescan', methods=['POST'])
@login_required
def rescan():
    db = get_db()
    scan_galaxies(app.config['GALFIT_BASE_PATH'], db)
    return redirect(url_for('sample_list'))


# --- App lifecycle ---

app.teardown_appcontext(close_db)


if __name__ == '__main__':
    init_db(app)
    with app.app_context():
        db = get_db()
        scan_galaxies(app.config['GALFIT_BASE_PATH'], db)
        init_analysis_data(app)
    app.run(debug=True, host='0.0.0.0', port=35091)
