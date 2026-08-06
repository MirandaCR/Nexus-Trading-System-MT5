"""
pandas-ta compatibility layer.

Handles:
  - NumPy 2.x removal of ``numpy.NaN`` (replaced by ``numpy.nan``).
  - Fallback to ``pandas_ta_classic`` when ``pandas_ta`` is not installed.
"""
from __future__ import annotations

import numpy as np

# NumPy 2.0 removed np.NaN — pandas_ta still uses it.  Restore it before
# importing so that ``from numpy import NaN as npNaN`` inside pandas_ta works.
if not hasattr(np, "NaN"):
    np.NaN = np.nan  # type: ignore[attr-defined]

try:
    import pandas_ta as ta  # type: ignore[import-untyped,import-not-found]
except ImportError:  # pragma: no cover
    try:
        import pandas_ta_classic as ta  # type: ignore[import-untyped,import-not-found]
    except ImportError:
        raise ImportError(
            "Neither 'pandas-ta' nor 'pandas-ta-classic' is installed. "
            "Install one with: pip install pandas-ta"
        )

__all__ = ["ta"]
