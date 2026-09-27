from .base import LengthBounds

# Derived from 2027 ar_NAV content (p5-p95 + buffer): reflexion 903-1111,
# oracion 839-983. The Latin default's oracion_max=900 sits below AR's own
# median (915), so it needs a wider ceiling; reflexion already fits within
# the Latin default but is restated here so AR's bounds are self-contained.
LANGS = ("ar",)
BOUNDS = LengthBounds(
    reflexion_min=900, reflexion_max=1150, oracion_min=550, oracion_max=1000
)
