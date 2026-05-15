import os
import json
from collections import OrderedDict


def _parse_sources():
    raw = os.environ.get('GALFIT_SOURCES', '').strip()
    if raw:
        return OrderedDict(json.loads(raw))
    return OrderedDict({'default': os.environ.get('GALFIT_BASE_PATH', os.path.expanduser('~/code/galfit_example'))})


class Config:
    GALFIT_BASE_PATH = os.environ.get('GALFIT_BASE_PATH', os.path.expanduser('~/code/galfit_example'))
    GALFIT_SOURCES = _parse_sources()
    DATABASE = os.environ.get(
        'DATABASE',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'galfit_viewer.db')
    )
    SECRET_KEY = 'galaxy-fitting-label-tool-secret-key'
    ANALYSIS_IMAGE_DIR = os.environ.get(
        'ANALYSIS_IMAGE_DIR',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analysis_data', 'images')
    )
