from .base import LengthBounds

# Derived from the published 2025 + 2026 ar_NAV corpus (365 entries each,
# near-identical: reflexion median ~2200 / p5-p95 1700-2950, oracion median
# ~1030 / p5-p95 790-1370), so Arabic readers keep the length they already
# know. Upper bounds are trimmed below p95 because 2026 had outliers up to
# 5050 chars. The 2027 NAV set generated under the Latin default (~1000 chars)
# was about half that length, so these bounds replace the earlier ones that
# had been derived from that 2027 file itself.
LANGS = ("ar",)
BOUNDS = LengthBounds(
    reflexion_min=1800,
    reflexion_max=2600,
    oracion_min=800,
    oracion_max=1300,
    # 2025/2026 AR reflexions run ~380 words. Gemma undershoots a bare
    # character range, so the word count is stated too.
    style_hint="Aim for about 380 words in total (never fewer than 330).",
)
