"""RSS -> keyword filter -> Put.io -> bangumi_dir pipeline.

Pure-logic modules with no FastAPI / dramatiq dependency — the actor and
API layers (``app/tasks/bt_*.py``, ``app/api/bt_api.py``) compose these.
"""

from __future__ import annotations
