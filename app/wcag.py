"""WCAG 2.x relative luminance -- the one implementation.

`app.contrast` builds its ratios on it and `app.overlay` samples the poster
with it (roadmap P6), so the luminance a badge row is chosen by and the
luminance its contrast is judged by are the same number. It lives in its own
module because `app.contrast` imports `app.overlay`, so the overlay cannot
import it from there.
"""

from __future__ import annotations


def _linearize(channel_8bit: int) -> float:
    c = channel_8bit / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


# Every 8-bit sRGB channel value, linearised once.
LINEAR_8BIT: tuple[float, ...] = tuple(_linearize(v) for v in range(256))


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (LINEAR_8BIT[c] for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# The luminance at which black and white text contrast equally with a
# background: (L + 0.05) / 0.05 == 1.05 / (L + 0.05). Above it a colour is
# "light" (dark text reads better on it), below it "dark".
LIGHT_DARK_MIDPOINT = (0.05 * 1.05) ** 0.5 - 0.05
