"""Commercial-managed FX rates (currency → Rial) for PI unit conversion."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone

from .models import CurrencyRate

STALE_AFTER = timedelta(hours=24)

# Seeded defaults the commercial manager must keep current.
BUILTIN_RATES: List[Dict[str, Any]] = [
    {"code": "usd", "name": "US Dollar", "symbol": "$", "rial_price": Decimal("0")},
    {"code": "eur", "name": "Euro", "symbol": "€", "rial_price": Decimal("0")},
]

# Searchable catalog for "Add currency" (code, name, symbol).
CURRENCY_CATALOG: List[Dict[str, str]] = [
    {"code": "gbp", "name": "British Pound", "symbol": "£"},
    {"code": "chf", "name": "Swiss Franc", "symbol": "CHF"},
    {"code": "aed", "name": "UAE Dirham", "symbol": "AED"},
    {"code": "try", "name": "Turkish Lira", "symbol": "₺"},
    {"code": "cny", "name": "Chinese Yuan", "symbol": "¥"},
    {"code": "jpy", "name": "Japanese Yen", "symbol": "¥"},
    {"code": "cad", "name": "Canadian Dollar", "symbol": "C$"},
    {"code": "aud", "name": "Australian Dollar", "symbol": "A$"},
    {"code": "sar", "name": "Saudi Riyal", "symbol": "SAR"},
    {"code": "kwd", "name": "Kuwaiti Dinar", "symbol": "KD"},
    {"code": "iqd", "name": "Iraqi Dinar", "symbol": "IQD"},
    {"code": "inr", "name": "Indian Rupee", "symbol": "₹"},
    {"code": "rub", "name": "Russian Ruble", "symbol": "₽"},
    {"code": "sek", "name": "Swedish Krona", "symbol": "kr"},
    {"code": "nok", "name": "Norwegian Krone", "symbol": "kr"},
    {"code": "dkk", "name": "Danish Krone", "symbol": "kr"},
    {"code": "sgd", "name": "Singapore Dollar", "symbol": "S$"},
    {"code": "hkd", "name": "Hong Kong Dollar", "symbol": "HK$"},
    {"code": "myr", "name": "Malaysian Ringgit", "symbol": "RM"},
    {"code": "thb", "name": "Thai Baht", "symbol": "฿"},
    {"code": "krw", "name": "South Korean Won", "symbol": "₩"},
    {"code": "zar", "name": "South African Rand", "symbol": "R"},
    {"code": "brl", "name": "Brazilian Real", "symbol": "R$"},
    {"code": "mxn", "name": "Mexican Peso", "symbol": "Mex$"},
    {"code": "pln", "name": "Polish Zloty", "symbol": "zł"},
    {"code": "czk", "name": "Czech Koruna", "symbol": "Kč"},
    {"code": "huf", "name": "Hungarian Forint", "symbol": "Ft"},
    {"code": "ron", "name": "Romanian Leu", "symbol": "lei"},
    {"code": "bgn", "name": "Bulgarian Lev", "symbol": "лв"},
    {"code": "omr", "name": "Omani Rial", "symbol": "OMR"},
    {"code": "qar", "name": "Qatari Riyal", "symbol": "QR"},
    {"code": "bhd", "name": "Bahraini Dinar", "symbol": "BD"},
    {"code": "azn", "name": "Azerbaijani Manat", "symbol": "₼"},
    {"code": "gel", "name": "Georgian Lari", "symbol": "₾"},
    {"code": "amd", "name": "Armenian Dram", "symbol": "֏"},
]


def normalize_code(code: str) -> str:
    c = str(code or "").strip().lower()
    if c in ("$", "dollar", "usdollar", "us$"):
        return "usd"
    if c in ("€", "euro"):
        return "eur"
    if c in ("rial", "irr", "ریال"):
        return "rial"
    return c


def ensure_builtin_rates() -> None:
    """Create USD / EUR rows if missing (idempotent)."""
    for item in BUILTIN_RATES:
        CurrencyRate.objects.get_or_create(
            code=item["code"],
            defaults={
                "name": item["name"],
                "symbol": item["symbol"],
                "rial_price": item["rial_price"],
                "is_builtin": True,
            },
        )
    # Mark existing USD/EUR as builtin even if created earlier without the flag.
    CurrencyRate.objects.filter(code__in=("usd", "eur")).update(is_builtin=True)


def latest_update_at():
    ensure_builtin_rates()
    return CurrencyRate.objects.order_by("-updated_at").values_list("updated_at", flat=True).first()


def is_rates_stale(now=None) -> bool:
    """True when the FX board is unusable for conversion.

    The board is stale when:
      • no rates exist / never updated
      • **any** currency with a price was last updated more than 24h ago
      • no positive Rial price exists yet
    """
    ensure_builtin_rates()
    now = now or timezone.now()
    rows = list(CurrencyRate.objects.all())
    if not rows:
        return True
    priced = [r for r in rows if (r.rial_price or 0) > 0]
    if not priced:
        return True
    cutoff = now - STALE_AFTER
    for r in priced:
        if not r.updated_at or r.updated_at < cutoff:
            return True
    return False


def hours_since_update(now=None) -> Optional[float]:
    latest = latest_update_at()
    if latest is None:
        return None
    now = now or timezone.now()
    return max(0.0, (now - latest).total_seconds() / 3600.0)


def rial_price_of(code: str) -> Decimal:
    """Rial amount for one unit of ``code``. Rial itself is 1."""
    code = normalize_code(code)
    if code == "rial":
        return Decimal("1")
    ensure_builtin_rates()
    row = CurrencyRate.objects.filter(code=code).first()
    if row is None:
        raise ValueError(f"Unknown currency: {code.upper()}")
    price = Decimal(row.rial_price or 0)
    if price <= 0:
        raise ValueError(f"No Rial price set for {code.upper()}. Ask Commercial manager to update FX rates.")
    return price


def conversion_rate(from_unit: str, to_unit: str) -> float:
    """Return rate in the PI convention: how many FROM units equal one TO unit.

    Example: USD at 1,700,000 Rial → Rial→USD rate = 1,700,000
             (1 TO-USD costs 1,700,000 FROM-Rial).
             USD→Rial rate = 1/1,700,000.
    """
    src = normalize_code(from_unit)
    dst = normalize_code(to_unit)
    if src == dst:
        return 1.0
    src_rial = rial_price_of(src)
    dst_rial = rial_price_of(dst)
    return float(dst_rial / src_rial)


def list_rates() -> List[CurrencyRate]:
    ensure_builtin_rates()
    return list(CurrencyRate.objects.all().order_by("code"))


def catalog_for_add() -> List[Dict[str, str]]:
    """Currencies not yet on the board, for the searchable add dropdown."""
    existing = set(CurrencyRate.objects.values_list("code", flat=True))
    existing.add("rial")
    return [c for c in CURRENCY_CATALOG if c["code"] not in existing]


def format_rial_amount(value) -> str:
    try:
        n = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        n = Decimal("0")
    # Integer-ish Rial with thousand separators.
    as_int = int(n.to_integral_value())
    return f"{as_int:,}"


def api_payload() -> Dict[str, Any]:
    """JSON payload for PI / commercial conversion UIs."""
    ensure_builtin_rates()
    stale = is_rates_stale()
    latest = latest_update_at()
    rates = []
    for row in list_rates():
        rates.append({
            "code": row.code,
            "name": row.name,
            "symbol": row.symbol or row.code.upper(),
            "rial_price": float(row.rial_price or 0),
            "rial_display": format_rial_amount(row.rial_price),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })
    # Always offer Rial as a conversion unit (not stored as a rate row).
    units = [{"code": "rial", "name": "Iranian Rial", "symbol": "Rial"}] + [
        {"code": r["code"], "name": r["name"], "symbol": r["symbol"]} for r in rates
    ]
    return {
        "ok": True,
        "stale": stale,
        "stale_hours": 24,
        "hours_since_update": hours_since_update(),
        "latest_update": latest.isoformat() if latest else None,
        "rates": rates,
        "units": units,
    }


def resolve_conversion(from_unit: str, to_unit: str) -> Tuple[float, bool]:
    """Return (rate, stale). Raises ValueError on bad/missing data.

    Callers must refuse conversion when ``stale`` is True.
    """
    stale = is_rates_stale()
    rate = conversion_rate(from_unit, to_unit)
    return rate, stale
