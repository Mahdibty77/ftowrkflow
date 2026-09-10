"""The public face of the coding engine — and the map of this package.

The old processor.py was one very large module. It was split by responsibility,
and this file re-exports what it used to expose, so views.py and the rest of the
project import from here and never had to change. Start reading here.

Where the work actually happens, in the order a row travels:

    excel_reader.py           read the four client columns out of the .xlsx
    excel_processor.py        the Build-TO loop over inquiry rows
    normalizers.py            reduce text to comparable tokens
    regex_patterns.py         load data.json / feature CSVs (mtime-cached)
    composite_keys.py         read JSON keys that carry ``||`` aliases
    feature_extractor.py      find the group, the type, the feature values
    find_size.py              map a raw size cell to NPS per group
    text_processor.py         THE pipeline; both upload and live editing enter here
    revision_set.py           ``set(var:value)`` overrides typed in Revision
    rule_engine.py            compatibility rules + offer → colours
    composite_features.py     keep ``&`` compound features coloured as one unit
    alarm_builder.py          which expected features are still missing
    final_arrange_builder.py  render FTCO DISCRIPTION and Filled_Features
    code_assigner.py          look the FT code up in the group's table
    table_layout_manager.py   extra CSV-driven columns, then display order
    calculation_engine.py     price / weight columns

Supporting cast, not on the row path: resource_paths (where data files live),
constants (the cache registry), cache_sync (tell the other gunicorn workers to
drop those caches), startup_warmup (fill them before the first request),
data_admin + item_builder + offer_builder (the reference-data admin screens).

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
from .rule_engine import apply_rules
from .text_processor import process_text_record, process_text_record_live

__all__ = [
    "apply_rules",
    "assign_code_from_csv",
    "build_alarms",
    "clean_for_group_and_features",
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
