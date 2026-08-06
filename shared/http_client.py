"""Shared HTTP client factory for all inter-service calls.

Services used to build a fresh ``httpx.AsyncClient`` per request. httpx
constructs an SSL context on each instantiation, and
``ssl.create_default_context()`` reads the whole system CA bundle off disk —
measured at ~1.4 s of CPU per client on this host.

A CPU profile of the API gateway attributed **95% of its CPU** to that single
call: it pinned the event loop at 100% of a core and pushed even local routes
like ``/health`` from ~10 ms to 1–3 s, which surfaced in the dashboard as
"gateway offline". nexus_agents creates 17 of these per evaluation cycle.

Every inter-service call is plain HTTP to 127.0.0.1, so one process-wide
context is enough. Passing an already-built ``SSLContext`` as ``verify`` makes
httpx skip the expensive construction entirely (~4000x faster to instantiate).
"""
from __future__ import annotations

import ssl
from typing import Any

import httpx

# Built once at import; reused by every client this process creates.
_SSL_CONTEXT = ssl.create_default_context()


def new_client(**kwargs: Any) -> httpx.AsyncClient:
    """``httpx.AsyncClient`` that reuses the shared SSL context.

    Accepts the same keyword arguments as ``httpx.AsyncClient``; an explicit
    ``verify`` passed by the caller still wins.
    """
    kwargs.setdefault("verify", _SSL_CONTEXT)
    return httpx.AsyncClient(**kwargs)
