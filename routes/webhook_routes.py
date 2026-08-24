"""Backward-compat shim; canonical module is routes/webhook/webhook_routes.py.

The canonical module replaces this object in sys.modules so legacy imports and
monkeypatches continue to operate on the same module after the upstream move.
"""

import sys as _sys

from routes.webhook import webhook_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
