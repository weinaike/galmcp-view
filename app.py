"""Galaxy Fitting Result Voting Web Application."""

import os
import json
from functools import wraps
import markdown
from flask import (Flask, render_template, request, redirect, url_for,
                   session, send_file, abort, Response)
from config import Config
from database import get_db, close_db, init_db
from scanner import scan_galaxies

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

@app.route('/')
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
        rounds_data.append({
            'round_number': r['round_number'],
            'timestamp_dir': r['timestamp_dir'],
            'has_png': r['png_path'] is not None,
            'has_summary': r['summary_path'] is not None,
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
    app.run(debug=True, host='0.0.0.0', port=35091)
