"""Highlight-related façade.

Current color decisions are calculated in rule_engine.py and rendered by
Initial_changes.py. This module exists as the customization entry point for
future highlight-only changes without touching extraction or view code.
"""

from .rule_engine import apply_rules, colored_display
from .Initial_changes import build_final_arrange_and_features, prepare_table_cell

__all__ = [
    "apply_rules",
    "colored_display",
    "build_final_arrange_and_features",
    "prepare_table_cell",
]
