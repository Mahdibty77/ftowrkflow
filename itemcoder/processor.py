"""Backward-compatible processor façade.

The old processor.py was very large, so the implementation is now split into
small responsibility-based modules. Existing imports keep working because this
file re-exports the public functions used by views.py and other project files.

View/url functions stay in views.py as requested.
"""

from .alarm_builder import build_alarms
from .code_assigner import assign_code_from_csv, load_code_resources
from .excel_processor import process_excel, process_excel_with_json, process_inquiry_records
from .excel_reader import read_excel_first_four_columns_fast
from .feature_extractor import (
    confind_size,
    find_group,
    find_group_features,
    find_phisic_feature,
    find_type,
)
from .normalizers import clean_for_group_and_features, preserve_original, remove_first_occurrence
from .regex_patterns import (
    load_feature_values,
    load_json_file,
    parse_csv_for_field,
    search_special_feature_in_original,
)
from .rule_engine import apply_rules, colored_display
from .text_processor import process_text_record, process_text_record_live

__all__ = [
    "apply_rules",
    "assign_code_from_csv",
    "build_alarms",
    "clean_for_group_and_features",
    "colored_display",
    "confind_size",
    "find_group",
    "find_group_features",
    "find_phisic_feature",
    "find_type",
    "load_code_resources",
    "load_feature_values",
    "load_json_file",
    "parse_csv_for_field",
    "preserve_original",
    "process_excel",
    "process_excel_with_json",
    "process_inquiry_records",
    "process_text_record",
    "process_text_record_live",
    "read_excel_first_four_columns_fast",
    "remove_first_occurrence",
    "search_special_feature_in_original",
]
