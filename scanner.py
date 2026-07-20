"""Scan galfit_example directory and parse fitting results into the database."""

import os
import re
import json
import glob


def find_analysis_report_path(galaxy_id, base_path):
    """Locate the analysis_report*.md file inside a galaxy directory.

    Returns the absolute path, or None if the galaxy dir is missing or has
    no matching report. Kept here (rather than in app.py) so the scanner can
    populate samples.best_turn without importing Flask; app.py re-exports
    these via underscore aliases."""
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


def parse_best_turn(galaxy_id, base_path):
    """Extract (best_turn, components) from the JSON block embedded in
    analysis_report.md.

    Returns a (timestamp_dir, components_list) tuple. timestamp_dir is None
    when the report is absent or carries no best_turn key; components_list is
    the accompanying type-name list (e.g. ['Disk','Bar','Companion']) or [].
    Both come from the same JSON object, so they are read in one file pass."""
    report_path = find_analysis_report_path(galaxy_id, base_path)
    if not report_path:
        return None, []
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError:
        return None, []
    m = re.search(r'"best_turn"\s*:\s*"([^"]+)"', content)
    best_turn = m.group(1) if m else None
    components = []
    cm = re.search(r'"components"\s*:\s*\[([^\]]*)\]', content)
    if cm:
        components = [
            c.strip().strip('"\'')
            for c in cm.group(1).split(',')
            if c.strip()
        ]
    return best_turn, components


def parse_summary(md_text):
    """Parse a summary.md file and extract components and chi-squared.

    Returns dict with:
        components: list of dicts [{type, x, y, mag, re, n, ba, pa}, ...]
        chi_squared_nu: float or None
    """
    result = {'components': [], 'chi_squared_nu': None, 'bic': None}

    # Extract Chi^2/nu
    chi_match = re.search(r'Chi\^2/nu\s*=\s*([\d.]+)', md_text)
    if chi_match:
        result['chi_squared_nu'] = float(chi_match.group(1))

    # Extract BIC
    bic_match = re.search(r'\|\s*BIC\s*\|\s*([\d.eE+\-]+)\s*\|', md_text)
    if bic_match:
        result['bic'] = float(bic_match.group(1))

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


def parse_gssummary(text):
    """Parse a GalfitS .gssummary file and extract components and statistics.

    Returns dict with:
        components: list of dicts [{type, params}, ...]
        chi_squared_nu: float or None
        bic: float or None
        per_band_chi2: list of dicts [{band, chisq, dof, reduced_chisq}, ...]
    """
    result = {
        'components': [],
        'chi_squared_nu': None,
        'bic': None,
        'per_band_chi2': [],
    }

    # Extract overall reduced chisq
    m = re.search(r'#\s*reduced chisq:\s*([\d.eE+\-]+)', text)
    if m:
        result['chi_squared_nu'] = float(m.group(1))

    # Extract overall BIC
    m = re.search(r'#\s*BIC:\s*([\d.eE+\-]+)', text)
    if m:
        result['bic'] = float(m.group(1))

    # Extract per-band data
    re_band = re.compile(
        r'#\s+image number:\s*\d+\s+band:\s*(\S+)\s+'
        r'chisq:\s*\[([\d.eE+\-]+)\]\s+'
        r'dof:\s*\[([\d.eE+\-]+)\]\s+'
        r'reduced chisq:\s*\[([\d.eE+\-]+)\]'
    )
    for m in re_band.finditer(text):
        result['per_band_chi2'].append({
            'band': m.group(1),
            'chisq': float(m.group(2)),
            'dof': float(m.group(3)),
            'reduced_chisq': float(m.group(4)),
        })

    # Extract free parameters grouped by component prefix
    # Known structural components: disk, bulge, bar (those with xcen/ycen/Re params)
    known_components = {'disk', 'bulge', 'bar', 'ring', 'psf', 'agn', 'lens'}

    free_match = re.search(
        r'# free parameters:\npname\s+best_value\n(.*?)(?:\n#|\Z)',
        text, re.DOTALL
    )
    if free_match:
        params_text = free_match.group(1)
        comp_params = {}
        for line in params_text.strip().split('\n'):
            parts = line.strip().split()
            if len(parts) >= 2:
                pname = parts[0]
                try:
                    pvalue = float(parts[1])
                except ValueError:
                    continue
                # Group by prefix (disk_, bulge_, bar_, etc.)
                if '_' in pname:
                    prefix, param = pname.split('_', 1)
                    if prefix in known_components:
                        comp_params.setdefault(prefix, {})[param] = pvalue

        for comp_name, params in comp_params.items():
            result['components'].append({
                'type': comp_name,
                'params': params,
            })

    return result


def scan_galaxies(source_label, base_path, db):
    """Scan base_path for galaxy directories and populate DB.

    Supports both single-band (archives/) and multi-band (output/) layouts:
    1. Detect fitting type per galaxy directory
    2. Find all timestamp subdirectories
    3. Sort chronologically, assign round numbers 1, 2, ...
    4. Parse summary from each round using the appropriate parser
    5. Upsert into samples and rounds tables

    base_path can be:
    - A parent directory containing multiple galaxy subdirectories
    - A single galaxy directory itself (with archives/ or output/ directly inside)
    """
    if not os.path.isdir(base_path):
        print(f"Warning: base path {base_path} does not exist")
        return

    # Check if base_path itself is a galaxy directory
    self_archives = os.path.join(base_path, 'archives')
    self_output = os.path.join(base_path, 'output')
    if os.path.isdir(self_archives) or os.path.isdir(self_output):
        # base_path is a single galaxy directory
        _scan_single_galaxy(source_label, base_path, base_path, db)
        return

    # Scan subdirectories as galaxy directories
    for entry in sorted(os.listdir(base_path)):
        galaxy_dir = os.path.join(base_path, entry)
        if os.path.isdir(galaxy_dir):
            _scan_single_galaxy(source_label, galaxy_dir, base_path, db)

    db.commit()


def _scan_single_galaxy(source_label, galaxy_dir, base_path, db):
    """Scan a single galaxy directory and upsert into DB."""
    # Detect fitting type: archives/ (single-band) or output/ (multi-band)
    archives_dir = os.path.join(galaxy_dir, 'archives')
    output_dir = os.path.join(galaxy_dir, 'output')

    if os.path.isdir(archives_dir):
        fitting_type = 'single-band'
        data_dir = archives_dir
    elif os.path.isdir(output_dir):
        fitting_type = 'multi-band'
        data_dir = output_dir
    else:
        return

    galaxy_id = os.path.basename(galaxy_dir)

    # Parse AI-recommended best_turn once per galaxy (None if no report / no key).
    best_turn, best_components = parse_best_turn(galaxy_id, base_path)

    # Get sorted timestamp directories
    timestamp_dirs = sorted([
        d for d in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, d))
    ])

    if not timestamp_dirs:
        return

    num_rounds = len(timestamp_dirs)

    # Upsert sample with fitting_type, best_turn and best_components
    db.execute(
        'INSERT INTO samples (source, galaxy_id, num_rounds, fitting_type, best_turn, best_components, last_scanned) '
        'VALUES (?, ?, ?, ?, ?, ?, datetime(\'now\')) '
        'ON CONFLICT(source, galaxy_id) DO UPDATE SET '
        'num_rounds=excluded.num_rounds, fitting_type=excluded.fitting_type, '
        'best_turn=excluded.best_turn, best_components=excluded.best_components, '
        'last_scanned=excluded.last_scanned',
        (source_label, galaxy_id, num_rounds, fitting_type, best_turn,
         json.dumps(best_components or []))
    )
    sample = db.execute(
        'SELECT id FROM samples WHERE source = ? AND galaxy_id = ?',
        (source_label, galaxy_id)
    ).fetchone()

    # Process each round
    for round_num, ts_dir in enumerate(timestamp_dirs, 1):
        round_path = os.path.join(data_dir, ts_dir)

        # Detect SED round
        is_sed = ts_dir.endswith('_sed') or '_sed_' in ts_dir

        # Find comparison PNG. Match by suffix so we accept outputs from
        # different GALFITS pipelines (galfit_comparison.png, imgblock_comparison.png, ...).
        if fitting_type == 'multi-band':
            png_files = glob.glob(os.path.join(round_path, 'all_bands_comparison.png'))
        else:
            png_files = glob.glob(os.path.join(round_path, '*comparison.png'))
        png_path = png_files[0] if png_files else None

        # Find and parse summary file (galfit_summary.md, imgblock_summary.md, ...).
        if fitting_type == 'multi-band':
            summary_files = glob.glob(os.path.join(round_path, '*.gssummary'))
        else:
            summary_files = glob.glob(os.path.join(round_path, '*summary.md'))

        chi_squared_nu = None
        bic = None
        components_json = None
        per_band_json = None

        if summary_files:
            try:
                with open(summary_files[0], 'r') as f:
                    text = f.read()
                if fitting_type == 'multi-band':
                    parsed = parse_gssummary(text)
                    per_band_json = json.dumps(parsed.get('per_band_chi2'))
                else:
                    parsed = parse_summary(text)
                chi_squared_nu = parsed.get('chi_squared_nu')
                bic = parsed.get('bic')
                components_json = json.dumps(parsed.get('components', []))
            except Exception as e:
                print(f"Warning: failed to parse {summary_files[0]}: {e}")

        summary_path = summary_files[0] if summary_files else None

        # Find image_fit PNG (multi-band only)
        image_fit_path = None
        if fitting_type == 'multi-band':
            image_fit_files = glob.glob(os.path.join(round_path, '*image_fit.png'))
            image_fit_path = image_fit_files[0] if image_fit_files else None

        # Determine round status
        if png_path is None and summary_path is None:
            round_status = 'failed'
        elif is_sed:
            round_status = 'sed'
        else:
            round_status = 'success'

        db.execute(
            'INSERT INTO rounds (sample_id, round_number, timestamp_dir, png_path, '
            'chi_squared_nu, bic, components_json, summary_path, '
            'fitting_type, round_status, is_sed, per_band_chi2_json, image_fit_path) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(sample_id, round_number) DO UPDATE SET '
            'timestamp_dir=excluded.timestamp_dir, png_path=excluded.png_path, '
            'chi_squared_nu=excluded.chi_squared_nu, '
            'bic=excluded.bic, '
            'components_json=excluded.components_json, '
            'summary_path=excluded.summary_path, '
            'fitting_type=excluded.fitting_type, '
            'round_status=excluded.round_status, '
            'is_sed=excluded.is_sed, '
            'per_band_chi2_json=excluded.per_band_chi2_json, '
            'image_fit_path=excluded.image_fit_path',
            (sample['id'], round_num, ts_dir, png_path,
             chi_squared_nu, bic, components_json, summary_path,
             fitting_type, round_status, int(is_sed), per_band_json,
             image_fit_path)
        )

    db.commit()
