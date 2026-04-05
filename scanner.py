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
    # Component types and their parameter columns (after x, y position):
    #   Full (7-param): sersic, expdisk, devauc, gaussian, king
    #     Line 1:  sersic : (x, y)  mag  Re  n  b/a  PA
    #     Line 2:          (dx,dy)  dm   dRe dn db/a dPA
    #   PSF (3-param): psf
    #     Line 1:  psf : (x, y)  mag
    #     Line 2:        (dx,dy) dm
    #   sky (different format, skipped):
    #     sky : [x, y]  value  [dx]  [dy]
    fitlog_match = re.search(
        r'## Fit log Content\n(.*?)(?:\n---|\Z)',
        md_text, re.DOTALL
    )
    if fitlog_match:
        log_text = fitlog_match.group(1)
        lines = log_text.split('\n')

        def _parse_val(s):
            """Parse a numeric value, stripping *, [], etc."""
            return float(s.replace('*', '').replace('[', '').replace(']', ''))

        # Regex to match the value line for full 7-param components
        re_full = re.compile(
            r'(sersic|expdisk|devauc|gaussian|king)\s*:\s*'
            r'\(\s*([\d.eE*+\-]+),\s*([\d.eE*+\-]+)\)\s+'
            r'([\d.eE*+\-]+)\s+([\d.eE*+\-]+)\s+'
            r'([\d.eE*\[\]]+)\s+([\d.eE*+\-]+)\s+([\d.eE*+\-]+)'
        )
        # Regex to match the error line for full 7-param components
        re_full_err = re.compile(
            r'\(\s*([\d.eE*+\-]+),\s*([\d.eE*+\-]+)\)\s+'
            r'([\d.eE*+\-]+)\s+([\d.eE*+\-]+)\s+'
            r'([\d.eE*\[\]]+)\s+([\d.eE*+\-]+)\s+([\d.eE*+\-]+)'
        )
        # Regex to match the value line for psf (3-param)
        re_psf = re.compile(
            r'(psf)\s*:\s*'
            r'\(\s*([\d.eE*+\-]+),\s*([\d.eE*+\-]+)\)\s+'
            r'([\d.eE*+\-]+)\s*$'
        )
        # Regex to match the error line for psf (3-param)
        re_psf_err = re.compile(
            r'\(\s*([\d.eE*+\-]+),\s*([\d.eE*+\-]+)\)\s+'
            r'([\d.eE*+\-]+)\s*$'
        )

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Try full 7-param components first
            m = re_full.match(line)
            if m:
                comp = {
                    'type': m.group(1),
                    'x': _parse_val(m.group(2)),
                    'y': _parse_val(m.group(3)),
                    'mag': _parse_val(m.group(4)),
                    're': _parse_val(m.group(5)),
                    'n': _parse_val(m.group(6)),
                    'ba': _parse_val(m.group(7)),
                    'pa': _parse_val(m.group(8)),
                }
                # Try to parse the next line as error/uncertainty values
                if i + 1 < len(lines):
                    err_line = lines[i + 1].strip()
                    err_m = re_full_err.match(err_line)
                    if err_m:
                        comp['x_err'] = _parse_val(err_m.group(1))
                        comp['y_err'] = _parse_val(err_m.group(2))
                        comp['mag_err'] = _parse_val(err_m.group(3))
                        comp['re_err'] = _parse_val(err_m.group(4))
                        comp['n_err'] = _parse_val(err_m.group(5))
                        comp['ba_err'] = _parse_val(err_m.group(6))
                        comp['pa_err'] = _parse_val(err_m.group(7))
                        i += 1  # skip error line
                result['components'].append(comp)
                i += 1
                continue

            # Try psf (3-param) components
            m = re_psf.match(line)
            if m:
                comp = {
                    'type': m.group(1),
                    'x': _parse_val(m.group(2)),
                    'y': _parse_val(m.group(3)),
                    'mag': _parse_val(m.group(4)),
                }
                # Try to parse the next line as error/uncertainty values
                if i + 1 < len(lines):
                    err_line = lines[i + 1].strip()
                    err_m = re_psf_err.match(err_line)
                    if err_m:
                        comp['x_err'] = _parse_val(err_m.group(1))
                        comp['y_err'] = _parse_val(err_m.group(2))
                        comp['mag_err'] = _parse_val(err_m.group(3))
                        i += 1  # skip error line
                result['components'].append(comp)
                i += 1
                continue

            i += 1

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

            summary_path = summary_files[0] if summary_files else None

            db.execute(
                'INSERT INTO rounds (sample_id, round_number, timestamp_dir, png_path, '
                'chi_squared_nu, components_json, summary_path) '
                'VALUES (?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(sample_id, round_number) DO UPDATE SET '
                'timestamp_dir=excluded.timestamp_dir, png_path=excluded.png_path, '
                'chi_squared_nu=excluded.chi_squared_nu, '
                'components_json=excluded.components_json, '
                'summary_path=excluded.summary_path',
                (sample['id'], round_num, ts_dir, png_path,
                 chi_squared_nu, components_json, summary_path)
            )

    db.commit()
