"""Scan galfit_example directory and parse fitting results into the database."""

import os
import re
import json
import glob


def parse_summary(md_text):
    """Parse a summary.md file and extract components and chi-squared.

    Returns dict with:
        components: list of dicts [{type, x, y, mag, re, n, ba, pa}, ...]
        chi_squared_nu: float or None
    """
    result = {'components': [], 'chi_squared_nu': None}

    # Extract Chi^2/nu
    chi_match = re.search(r'Chi\^2/nu\s*=\s*([\d.]+)', md_text)
    if chi_match:
        result['chi_squared_nu'] = float(chi_match.group(1))

    # Extract components from "## Fit log Content" section
    # Format:
    #   sersic    : (  201.44,   200.55)   24.31      2.46    5.16    0.66    56.06
    #   sky       : [200.50, 200.50]  4.81e-05  [0.00e+00]  [0.00e+00]
    fitlog_match = re.search(
        r'## Fit log Content\n(.*?)(?:\n---|\Z)',
        md_text, re.DOTALL
    )
    if fitlog_match:
        log_text = fitlog_match.group(1)

        # Match sersic lines: type : (x, y) mag re n ba pa
        for line in log_text.split('\n'):
            line = line.strip()
            m = re.match(
                r'(sersic|expdisk|devauc|gaussian|king)\s*:\s*'
                r'\(\s*([\d.*]+),\s*([\d.*]+)\)\s+'
                r'([\d.*]+)\s+([\d.*]+)\s+'
                r'([\d.*\[\]]+)\s+([\d.*]+)\s+([\d.*]+)',
                line
            )
            if m:
                comp = {
                    'type': m.group(1),
                    'x': float(m.group(2).replace('*', '')),
                    'y': float(m.group(3).replace('*', '')),
                    'mag': float(m.group(4).replace('*', '')),
                    're': float(m.group(5).replace('*', '')),
                    'n': float(m.group(6).replace('*', '').replace('[', '').replace(']', '')),
                    'ba': float(m.group(7).replace('*', '')),
                    'pa': float(m.group(8).replace('*', '')),
                }
                result['components'].append(comp)

    return result


def scan_galaxies(base_path, db):
    """Scan base_path for galaxy directories with archives and populate DB.

    For each directory containing an archives/ subdirectory:
    1. Find all timestamp subdirectories
    2. Sort chronologically, assign round numbers 1, 2, ...
    3. Parse summary.md from each round
    4. Upsert into samples and rounds tables
    """
    if not os.path.isdir(base_path):
        print(f"Warning: base path {base_path} does not exist")
        return

    for entry in sorted(os.listdir(base_path)):
        archives_dir = os.path.join(base_path, entry, 'archives')
        if not os.path.isdir(archives_dir):
            continue

        galaxy_id = entry

        # Get sorted timestamp directories
        timestamp_dirs = sorted([
            d for d in os.listdir(archives_dir)
            if os.path.isdir(os.path.join(archives_dir, d))
        ])

        if not timestamp_dirs:
            continue

        num_rounds = len(timestamp_dirs)

        # Upsert sample
        db.execute(
            'INSERT INTO samples (galaxy_id, num_rounds, last_scanned) '
            'VALUES (?, ?, datetime(\'now\')) '
            'ON CONFLICT(galaxy_id) DO UPDATE SET '
            'num_rounds=excluded.num_rounds, last_scanned=excluded.last_scanned',
            (galaxy_id, num_rounds)
        )
        sample = db.execute(
            'SELECT id FROM samples WHERE galaxy_id = ?', (galaxy_id,)
        ).fetchone()

        # Process each round
        for round_num, ts_dir in enumerate(timestamp_dirs, 1):
            round_path = os.path.join(archives_dir, ts_dir)

            # Find PNG file
            png_files = glob.glob(os.path.join(round_path, '*_galfit_comparison.png'))
            png_path = png_files[0] if png_files else None

            # Find and parse summary file
            summary_files = glob.glob(os.path.join(round_path, '*_galfit_summary.md'))
            chi_squared_nu = None
            components_json = None

            if summary_files:
                try:
                    with open(summary_files[0], 'r') as f:
                        md_text = f.read()
                    parsed = parse_summary(md_text)
                    chi_squared_nu = parsed['chi_squared_nu']
                    components_json = json.dumps(parsed['components'])
                except Exception as e:
                    print(f"Warning: failed to parse {summary_files[0]}: {e}")

            db.execute(
                'INSERT INTO rounds (sample_id, round_number, timestamp_dir, png_path, '
                'chi_squared_nu, components_json) '
                'VALUES (?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(sample_id, round_number) DO UPDATE SET '
                'timestamp_dir=excluded.timestamp_dir, png_path=excluded.png_path, '
                'chi_squared_nu=excluded.chi_squared_nu, '
                'components_json=excluded.components_json',
                (sample['id'], round_num, ts_dir, png_path,
                 chi_squared_nu, components_json)
            )

    db.commit()
