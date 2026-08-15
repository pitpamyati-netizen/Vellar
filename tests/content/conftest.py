"""Content is loaded once per test session - it is immutable, so sharing is safe."""

from __future__ import annotations

# The black list from Narrative.md, section 2, as stems: one word here is enough
# to make a name sound like the generic fantasy set the world is written against.
FORBIDDEN_WORDS = (
    "вечн",
    "древн",
    "проклят",
    "тёмн властелин",
    "кровав",
    "бездн",
    "пустот",
    "реальност",
    "судьб",
    "избранн",
    "легендарн",
)
