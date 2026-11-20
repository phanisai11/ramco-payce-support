"""Elastic Beanstalk entrypoint — exposes the FastAPI app for Procfile/uvicorn.

The package lives under ``src/`` (hatch layout); EB's Python platform does
not install the project itself, so we put ``src`` on sys.path and import
the already-constructed ``app`` object.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from payce_support.api.app import app  # noqa: E402,F401
