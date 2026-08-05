"""Document-number construction and the global case serial counter.

Document number layout (worked example FT-IN-503-102-015-1254-00):

    FT   - company prefix (Foolad Tabar), constant
    IN   - document kind token (Indent / Tender / Budget -> IN / TE / BU)
    503  - year+month: last digit(s) of the Jalali year + 2-digit month
           (1405/03 -> "5" + "03" -> "503")
    102  - expert code of the creator
    015  - client (kar-farma) code
    1254 - global, system-wide sequential case serial
    00   - version (per exported form)

Export file names insert the form token after the company prefix, e.g.:
    FT-TO-IN-503-102-015-1254-00   (Technical Offer, version 00)
    FT-PI-IN-503-102-015-1254-01   (Proforma, version 01)
"""
from __future__ import annotations

from django.conf import settings
from django.db import transaction

from .constants import DocKind, FormKind
from .jalali import jalali_year_month

COMPANY_PREFIX = getattr(settings, "FT_COMPANY_PREFIX", "FT")
# How many trailing digits of the Jalali year to use. The business example
# uses a single digit ("503" for 1405/03); change to 2 for "0503" style.
YEAR_DIGITS = getattr(settings, "FT_YEAR_DIGITS", 1)
SERIAL_MIN_WIDTH = getattr(settings, "FT_SERIAL_MIN_WIDTH", 4)


def year_month_token() -> str:
    """Return the live year+month token, e.g. '503'."""
    jy, jm = jalali_year_month()
    year_part = str(jy)[-YEAR_DIGITS:]
    return f"{year_part}{jm:02d}"


def format_version(version: int) -> str:
    """Format a version integer as a 2-digit token (0 -> '00')."""
    try:
        return f"{int(version):02d}"
    except Exception:
        return "00"


def build_doc_no(*, ym: str, expert_code: str, client_code: str,
                 serial: int, kind: str = "", version: int = 0) -> str:
    """Build the document number.

    Only the client/expert/serial and year-month matter for the code; the
    document kind and the version are NOT part of it. Worked example:
    client 017, expert 101, ym 503, serial 1256 -> ``FT-503-101-017-1256``.
    (``kind`` / ``version`` are accepted for backwards compatibility but unused.)
    """
    return "-".join([
        COMPANY_PREFIX,
        ym,
        str(expert_code or "").strip(),
        str(client_code or "").strip(),
        str(serial).rjust(SERIAL_MIN_WIDTH, "0"),
    ])


def build_export_name(*, form_kind: str, kind: str, ym: str, expert_code: str,
                       client_code: str, serial: int, version: int = 0,
                       group_suffix: str = "") -> str:
    """Build the export file name (with TO/PI/INQ token and optional group)."""
    name = "-".join([
        COMPANY_PREFIX,
        FormKind.EXPORT_TOKEN.get(form_kind, "TO"),
        DocKind.TOKEN.get(kind, "IN"),
        ym,
        str(expert_code or "").strip(),
        str(client_code or "").strip(),
        str(serial).rjust(SERIAL_MIN_WIDTH, "0"),
        format_version(version),
    ])
    if group_suffix:
        name = f"{name}-{group_suffix}"
    return name


@transaction.atomic
def next_case_serial() -> int:
    """Return the next global, system-wide case serial.

    A dedicated row keeps the counter monotonic even with several users
    creating cases at the same time (the row is locked for update).
    """
    from .models import SerialCounter

    counter, _ = SerialCounter.objects.select_for_update().get_or_create(
        key="case_serial", defaults={"value": 1802},
    )
    counter.value += 1
    counter.save(update_fields=["value"])
    return counter.value
