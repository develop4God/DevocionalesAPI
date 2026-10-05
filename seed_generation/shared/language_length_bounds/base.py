from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LengthBounds:
    reflexion_min: int
    reflexion_max: int
    oracion_min: int
    oracion_max: int
    # Optional extra reflexion instruction for a language whose published
    # content has a specific shape (e.g. multi-paragraph). Empty = none.
    style_hint: str = ""


# Derived from real 2025/2026 es/pt content (p5-p95), verified at 98-99.7%
# pass rate against es_NVI, es (RVR1960), pt_ARC, pt_NVI. Scoped to
# Latin-script languages; used as the fallback for any language without its
# own calibrated bounds file in this package.
LATIN_DEFAULT = LengthBounds(
    reflexion_min=900, reflexion_max=1350, oracion_min=550, oracion_max=900
)
