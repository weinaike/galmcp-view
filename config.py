import os
import json
from collections import OrderedDict


def _parse_sources():
    raw = os.environ.get('GALFIT_SOURCES', '').strip()
    if raw:
        return OrderedDict(json.loads(raw))
    return OrderedDict({'default': os.environ.get('GALFIT_BASE_PATH', os.path.expanduser('~/code/galfit_example'))})


def _parse_parent_dirs():
    """Parse GALFIT_PARENT_DIRS env var: 'name1:/path1,name2:/path2' -> OrderedDict."""
    raw = os.environ.get('GALFIT_PARENT_DIRS', '').strip()
    if raw:
        result = OrderedDict()
        for item in raw.split(','):
            if ':' in item:
                name, path = item.split(':', 1)
                result[name.strip()] = path.strip()
        return result
    return OrderedDict({'galfit': '/data/galfit', 'galfits': '/data/galfits'})


class Config:
    GALFIT_BASE_PATH = os.environ.get('GALFIT_BASE_PATH', os.path.expanduser('~/code/galfit_example'))
    GALFIT_SOURCES = _parse_sources()
    GALFIT_PARENT_DIRS = _parse_parent_dirs()
    DATABASE = os.environ.get(
        'DATABASE',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'galfit_viewer.db')
    )
    SECRET_KEY = 'galaxy-fitting-label-tool-secret-key'
    ANALYSIS_IMAGE_DIR = os.environ.get(
        'ANALYSIS_IMAGE_DIR',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analysis_data', 'images')
    )
    # visualRAG KB service linkage (distillation + ingestion from the labeling UI).
    # VISUALRAG_SERVICE_URL empty => linkage disabled (badge red, ingest disabled).
    VISUALRAG_SERVICE_URL = os.environ.get('VISUALRAG_SERVICE_URL', '')
    VISUALRAG_ENABLED = os.environ.get('VISUALRAG_ENABLED', '1')
