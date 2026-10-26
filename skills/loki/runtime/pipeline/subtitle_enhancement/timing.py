"""Word-evidence-only subtitle presentation timing (approved F1 policy).

This module neither runs ASR nor changes transcript tokens. Missing, malformed,
or conflicting evidence retains the original cues. Times use one source clock.
"""

from __future__ import annotations

import math

from numbers import Real

def _finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
