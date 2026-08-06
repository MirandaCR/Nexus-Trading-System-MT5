"""Gateway-local alias for the shared HTTP client factory.

The canonical implementation lives in ``shared/http_client.py`` so every
service reuses one SSL context policy; this module keeps the gateway's
``from ..http_client import new_client`` imports working.
"""
from __future__ import annotations

from shared.http_client import new_client

__all__ = ["new_client"]
