import os


class Config:
    GALFIT_BASE_PATH = os.environ.get('GALFIT_BASE_PATH', os.path.expanduser('~/code/galfit_example'))
    DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'galfit_viewer.db')
    SECRET_KEY = 'galaxy-fitting-label-tool-secret-key'
    ANALYSIS_IMAGE_DIR = os.environ.get(
        'ANALYSIS_IMAGE_DIR',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analysis_data', 'images')
    )
