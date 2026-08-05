"""Field validation for personnel records."""
from django.core.exceptions import ValidationError


def normalize_digits(value: str) -> str:
    """Fold Persian/Arabic-Indic digits to ASCII and strip separators.

    Someone typing a national ID on a Persian keyboard produces ۰۱۲۳…, and
    people habitually add dashes or spaces. Storing whichever form happened to
    be typed would make the uniqueness check meaningless — the same ID entered
    two ways would land as two different rows. Everything is folded to plain
    ASCII digits before it is validated or stored.
    """
    if not value:
        return ""
    persian = "۰۱۲۳۴۵۶۷۸۹"
    arabic = "٠١٢٣٤٥٦٧٨٩"
    out = []
    for ch in str(value).strip():
        if ch in persian:
            out.append(str(persian.index(ch)))
        elif ch in arabic:
            out.append(str(arabic.index(ch)))
        elif ch.isdigit():
            out.append(ch)
        elif ch in " -_/.":
            continue
        else:
            out.append(ch)  # kept so validation can reject it with a clear message
    return "".join(out)


def validate_national_id(value: str) -> str:
    """Validate an Iranian national ID (کد ملی) and return it normalised.

    Ten digits, where the last one is a check digit derived from the first
    nine. Validating properly rather than just counting characters matters
    here: this is the field that decides whether two records are the same
    human, and a typo that merely looks like an ID would create a duplicate
    person that nothing downstream could reconcile.

    Numbers made of a single repeated digit (0000000000, 1111111111, …) pass
    the arithmetic but are not real IDs, so they are rejected explicitly.
    """
    digits = normalize_digits(value)
    if not digits:
        return ""

    if not digits.isdigit():
        raise ValidationError("A national ID may contain digits only.")
    if len(digits) != 10:
        raise ValidationError("A national ID must be exactly 10 digits.")
    if digits == digits[0] * 10:
        raise ValidationError("This is not a valid national ID.")

    checksum = sum(int(digits[i]) * (10 - i) for i in range(9))
    remainder = checksum % 11
    expected = remainder if remainder < 2 else 11 - remainder
    if int(digits[9]) != expected:
        raise ValidationError(
            "This national ID is not valid — please check the digits."
        )
    return digits


def validate_iban(value: str) -> str:
    """Validate an Iranian IBAN (شبا) and return it normalised to IR + 24 digits.

    Checked properly rather than by length alone, using the standard mod-97
    test: this is the number a salary is paid into, and a transposed pair of
    digits is both easy to make and expensive to discover. Typing it with or
    without the "IR", with spaces, or with Persian digits all work.
    """
    from django.core.exceptions import ValidationError

    if not value:
        return ""
    raw = normalize_digits(str(value)).upper().replace("IR", "", 1) \
        if str(value).strip().upper().startswith("IR") else normalize_digits(str(value))
    raw = "".join(ch for ch in raw if ch.isalnum())
    if not raw.isdigit() or len(raw) != 24:
        raise ValidationError("شماره شبا باید ۲۴ رقم پس از IR باشد.")

    # Standard IBAN check: move the country code and check digits to the end,
    # map letters to numbers (I=18, R=27), and the whole thing mod 97 must be 1.
    rearranged = raw[2:] + "1827" + raw[:2]
    if int(rearranged) % 97 != 1:
        raise ValidationError("شماره شبا معتبر نیست — ارقام را بررسی کنید.")
    return "IR" + raw
