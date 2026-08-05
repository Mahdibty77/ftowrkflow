"""Per-unit visual theming.

Each organisational unit gets its own professional accent colour so users can
tell at a glance which unit a screen, card or case currently belongs to. The
palette is intentionally muted and engineering-flavoured (no neon), and every
theme stays light enough to keep long tables readable.

Keys match accounts.constants.Unit values plus an "admin" fallback.
"""

UNIT_THEMES = {
    "COMMERCIAL": {
        "label": "Commercial",
        "accent": "#b07514",        # refined bronze / amber
        "accent_strong": "#8a5a0f",
        "accent_soft": "#f7efe0",
        "on_accent": "#ffffff",
    },
    "TECHNICAL": {
        "label": "Technical",
        "accent": "#1f5f8b",        # engineer blue
        "accent_strong": "#164863",
        "accent_soft": "#e8f1f8",
        "on_accent": "#ffffff",
    },
    "SUPPLY": {
        "label": "Supply",
        "accent": "#1f7a5a",        # procurement teal-green
        "accent_strong": "#155c43",
        "accent_soft": "#e6f3ee",
        "on_accent": "#ffffff",
    },
    "ADMIN": {
        "label": "Administration",
        "accent": "#403c8c",        # slate indigo
        "accent_strong": "#2f2c6b",
        "accent_soft": "#ecebf7",
        "on_accent": "#ffffff",
    },
}

DEFAULT_THEME = UNIT_THEMES["ADMIN"]


def theme_for_unit(unit_code: str) -> dict:
    """Return the theme dict for a unit code, falling back to the admin theme."""
    if not unit_code:
        return DEFAULT_THEME
    return UNIT_THEMES.get(str(unit_code).upper(), DEFAULT_THEME)
