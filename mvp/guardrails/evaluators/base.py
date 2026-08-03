"""The evaluator adapter contract (RT-4).

This is the only seam in the runtime where engine-specific logic is permitted.
Every adapter — in-process or managed — returns the same `Finding` shape, so
nothing downstream can observe which engine produced a detection.

The reason code's middle segment is the *detection family from the policy*, not
the implementation name. See ADR-0003 vs INTEGRATION-ARCHITECTURE.md section 3.5:
the taxonomy ADR says the segment names the evaluator that produced the finding,
while the integration document requires codes to be stable across engines
covering the same category. Both cannot hold at once. This implementation takes
the integration document's rule — codes stay engine-stable — and records the
implementation identity in `evaluator_metadata` instead, where it is queryable
without destabilising the code. Flagged as a documentation conflict to resolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..contract import reason_code
from ..policy import EvaluatorSelection


@dataclass(frozen=True)
class Finding:
    """One evaluator's normalized output for one selection."""

    family: str
    capability: str
    category: str
    matched: bool
    confidence: float
    evaluator_name: str
    evaluator_version: str
    # Character spans of the matched content, used to satisfy a redact action.
    # Empty when the evaluator cannot localize its detection.
    spans: tuple[tuple[int, int], ...] = ()

    @property
    def reason_code(self) -> str:
        return reason_code(self.category, self.family, self.capability)


class Evaluator(Protocol):
    """What every adapter must implement, whatever it wraps."""

    name: str
    version: str

    def evaluate(self, text: str, selection: EvaluatorSelection) -> Finding: ...
