"""Fast Excel reading helpers.

This file is responsible only for reading the first useful columns from the
uploaded workbook. Processing each row is handled by excel_processor.py.
"""

import re
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd


def _column_letters(cell_ref):
    m = re.match(r"([A-Z]+)", cell_ref or "")
    return m.group(1) if m else ""


def _load_shared_strings_from_xlsx(zip_file):
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    shared_strings = []

    if "xl/sharedStrings.xml" not in zip_file.namelist():
        return shared_strings

    root = ET.fromstring(zip_file.read("xl/sharedStrings.xml"))
    for si in root.findall(ns + "si"):
        parts = []
        for t in si.iter(ns + "t"):
            parts.append(t.text or "")
        shared_strings.append("".join(parts))

    return shared_strings


def _cell_value(cell, shared_strings):
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    value_node = cell.find(ns + "v")
    value = "" if value_node is None else (value_node.text or "")

    cell_type = cell.attrib.get("t")
    if cell_type == "s" and value != "":
        try:
            return shared_strings[int(value)]
        except Exception:
            return value

    if cell_type == "inlineStr":
        text_parts = []
        for t in cell.iter(ns + "t"):
            text_parts.append(t.text or "")
        return "".join(text_parts)

    return value


def read_excel_first_four_columns_fast(uploaded_file, blank_a_stop=500):
    """
    خواندن سریع چهار ستون اول فایل xlsx.

    دلیل این تابع: بعضی فایل‌ها ظاهراً ۵۰۰ ردیف دارند، اما داخل Excel تا ردیف
    1,048,576 مقدار/فرمت ذخیره شده است. pandas.read_excel مجبور می‌شود همه را
    بخواند و آپلود بسیار کند می‌شود. این تابع فقط ردیف‌هایی را نگه می‌دارد که
    ستون A آن‌ها مقدار دارد؛ یعنی همان توضیح کالا که منطق برنامه بر اساس آن است.
    """
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    with zipfile.ZipFile(uploaded_file) as zf:
        shared_strings = _load_shared_strings_from_xlsx(zf)

        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in zf.namelist():
            sheet_candidates = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
            if not sheet_candidates:
                return pd.DataFrame()
            sheet_name = sorted(sheet_candidates)[0]

        headers = None
        rows = []
        data_started = False
        blank_a_streak = 0

        with zf.open(sheet_name) as sheet_file:
            for _event, elem in ET.iterparse(sheet_file, events=("end",)):
                if elem.tag != ns + "row":
                    continue

                values = {"A": "", "B": "", "C": "", "D": ""}

                for cell in elem.findall(ns + "c"):
                    col = _column_letters(cell.attrib.get("r", ""))
                    if col in values:
                        values[col] = _cell_value(cell, shared_strings)

                row_values = [values["A"], values["B"], values["C"], values["D"]]
                has_description = str(values["A"]).strip() != ""

                if headers is None:
                    if has_description:
                        headers = row_values
                    elem.clear()
                    continue

                if has_description:
                    data_started = True
                    blank_a_streak = 0
                    rows.append(row_values)
                elif data_started:
                    blank_a_streak += 1
                    if blank_a_streak >= blank_a_stop:
                        elem.clear()
                        break

                elem.clear()

    if headers is None:
        return pd.DataFrame()

    normalized_headers = []
    for i, h in enumerate(headers):
        h = str(h).strip() if h is not None else ""
        normalized_headers.append(h or f"Column_{i + 1}")

    return pd.DataFrame(rows, columns=normalized_headers)
