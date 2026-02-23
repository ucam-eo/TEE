import os

mode = os.environ.get('TEE_MODE', 'desktop')
if mode == 'production':
    from .production import *
else:
    from .desktop import *
