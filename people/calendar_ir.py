"""Iranian public-holiday calendar, generated arithmetically for any year.

This module exists because the holiday list used to be a hand-typed table that
simply ran out: it was partial for Jalali 1406 and empty from 1407 on, and a
holiday nobody had typed in yet was silently planned as an ordinary working day,
so every employee was measured against hours that could not have been worked.
A table can only ever be as current as the last person who edited it, so the
calendar is computed here instead and no longer needs an annual edit.

Two kinds of holiday, two very different confidence levels:

* SOLAR holidays sit on a fixed Jalali (month, day) forever -- Nowruz is always
  1 Farvardin. They are exact for every year, past and future, and are marked
  ``confirmed=True``.

* LUNAR holidays sit on a fixed Hijri (month, day) and therefore drift about
  eleven days a year against the solar calendar. We compute them with the
  standard *arithmetic* (tabular) Islamic calendar, which is pure integer maths
  and needs no data file or network call. But Iran does not use the arithmetic
  calendar to fix its holidays -- it uses actual moon sighting, announced only
  days beforehand -- so a computed lunar date can land a day or two off the date
  the country eventually observes. Measured against the confirmed rows this
  project already had (Jalali 1403-1405) the arithmetic calendar was exact or
  within one day for most holidays and within three days for the rest.
  Everything generated here is therefore marked ``confirmed=False``, and
  :mod:`people.iran_holidays` carries an override table where an official
  announcement replaces the computed guess. Never present a ``confirmed=False``
  date to a user as though it were authoritative.

Nothing in this module is hand-typed per year. Jalali <-> Gregorian conversion
is delegated to :mod:`cases.jalali`, which is the project's single source of
truth for that; we never re-implement it.
"""

from __future__ import annotations

import datetime
from functools import lru_cache
from typing import NamedTuple

from cases.jalali import gregorian_to_jalali, jalali_to_gregorian

# ---------------------------------------------------------------------------
# Arithmetic (tabular) Islamic calendar
# ---------------------------------------------------------------------------
#
# The tabular calendar alternates 30- and 29-day months (Muharram 30, Safar 29,
# ...) and adds a 30th day to Dhu al-Hijjah in 11 of every 30 years. That 30-year
# cycle is what keeps it within a day or so of the real moon over centuries.
#
# Day 0 of the count is 1 Muharram 1 AH. Which Gregorian day that is depends on
# which variant you pick: the "astronomical" epoch is Thursday 15 July 622 CE
# (Julian) and the "civil" one is the Friday after. We use the astronomical
# epoch, JDN 1948439. What that choice rests on, stated exactly, because the
# two variants differ by one day and one day is the whole question here:
#
# * Three of the four rows the old hardcoded table held for Jalali 1406 convert
#   back to identifiable Hijri dates -- 17 Mordad aside, their spacing (0, 8, 17
#   days apart) matches 20 Safar, 28 Safar and 8 Rabi I and nothing else. The
#   astronomical epoch reproduces all three exactly; the civil epoch is one day
#   late on all three. That is the comparison that picked the epoch.
# * Scored more broadly -- every computed lunar date for 1403-1406 against the
#   nearest recorded day off in the OFFICIAL_* tables -- the two variants are
#   effectively tied: astronomical 22 exact of 75 and 41 within one day, civil
#   23 and 44. That aggregate does NOT choose between them, and it should not be
#   quoted as though it did; the published lists also contain government
#   closures that are not lunar holidays at all and omit holidays that are, so
#   "nearest recorded day off" is a noisy score. It is reported here only so the
#   next person does not have to re-derive it and conclude the choice was
#   better supported than it is.
#
# What was NOT checked: no ephemeris, sighting record or external calendar was
# consulted, and neither epoch is claimed to predict what Iran will announce.
# Sighting can move a date either way regardless of epoch, which is exactly why
# every lunar date generated here is confirmed=False and why the override tables
# in people/iran_holidays.py exist.
#
# To re-derive: rebind HIJRI_EPOCH_JDN, call generate_year.cache_clear(), and
# compare against the OFFICIAL_* tables in people/iran_holidays.py rather than
# against a textbook; those are the dates this business actually observed.
HIJRI_EPOCH_JDN = 1948439

# datetime.date counts proleptic-Gregorian days from 0001-01-01 == ordinal 1,
# which is JDN 1721426. Converting through ordinals lets the standard library
# do all the Gregorian leap-year work for us.
_JDN_MINUS_ORDINAL = 1721425


def is_hijri_leap_year(hy: int) -> bool:
    """True when Dhu al-Hijjah of Hijri year ``hy`` has 30 days instead of 29.

    Eleven years in each 30-year cycle are leap. The classic leap set is
    {2, 5, 7, 10, 13, 16, 18, 21, 24, 26, 29}; the modulo below is the closed
    form of exactly that set, so there is no list to keep in sync.
    """
    return (11 * hy + 14) % 30 < 11


def hijri_month_length(hy: int, hm: int) -> int:
    """Length of Hijri month ``hm`` in year ``hy`` under the tabular calendar."""
    if hm == 12:
        return 30 if is_hijri_leap_year(hy) else 29
    # Odd months are 30 days, even months 29 -- the alternation that makes the
    # mean tabular month 29.53 days, very close to a real lunation.
    return 30 if hm % 2 == 1 else 29


def hijri_to_jdn(hy: int, hm: int, hd: int) -> int:
    """Julian Day Number of a tabular-Hijri date.

    ``29 * (hm - 1) + hm // 2`` is the closed form of "sum of the lengths of the
    months before this one" given the 30/29 alternation, and
    ``(11 * (hy - 1) + 14) // 30`` counts the leap days accumulated in whole
    years before this one.
    """
    return (
        HIJRI_EPOCH_JDN
        + 354 * (hy - 1)
        + (11 * (hy - 1) + 14) // 30
        + 29 * (hm - 1)
        + hm // 2
        + (hd - 1)
    )


def jdn_to_hijri(jdn: int) -> tuple[int, int, int]:
    """Inverse of :func:`hijri_to_jdn`.

    The first guess comes from the mean tabular year (10631 days per 30 years);
    the two while-loops then correct it. They run at most once or twice, so this
    stays cheap even though it is not a closed form.
    """
    days = jdn - HIJRI_EPOCH_JDN
    hy = (30 * days + 10646) // 10631
    while hijri_to_jdn(hy, 1, 1) > jdn:
        hy -= 1
    while hijri_to_jdn(hy + 1, 1, 1) <= jdn:
        hy += 1
    hm = 1
    while hm < 12 and hijri_to_jdn(hy, hm + 1, 1) <= jdn:
        hm += 1
    hd = jdn - hijri_to_jdn(hy, hm, 1) + 1
    return hy, hm, hd


def gregorian_to_jdn(when: datetime.date) -> int:
    return when.toordinal() + _JDN_MINUS_ORDINAL


def jdn_to_gregorian(jdn: int) -> datetime.date:
    return datetime.date.fromordinal(jdn - _JDN_MINUS_ORDINAL)


def hijri_to_gregorian(hy: int, hm: int, hd: int) -> datetime.date:
    return jdn_to_gregorian(hijri_to_jdn(hy, hm, hd))


def gregorian_to_hijri(when: datetime.date) -> tuple[int, int, int]:
    return jdn_to_hijri(gregorian_to_jdn(when))


# ---------------------------------------------------------------------------
# The holiday definitions themselves
# ---------------------------------------------------------------------------


class Holiday(NamedTuple):
    """One official day off, already resolved onto a Jalali (month, day).

    ``confirmed`` is the honest bit: True means the date cannot move (a solar
    holiday, or a lunar one whose official announcement we have recorded), False
    means we computed it from the arithmetic lunar calendar and Iran may yet
    announce a neighbouring day.
    """

    jmonth: int
    jday: int
    key: str
    name_en: str
    name_fa: str
    lunar: bool
    confirmed: bool


# Solar holidays: same Jalali (month, day) every year, forever, so these are
# exact and need no override mechanism. This list was checked against the
# hardcoded table this module replaces and agrees with it exactly -- same ten
# (month, day) pairs, no additions and no corrections. Note that 29 Esfand
# stays 29 Esfand in a leap year; the old table's "(leap: also 30)" aside never
# actually added 30 Esfand, and we do not add it either, because the existing
# data is the ground truth for what this business observes.
SOLAR_HOLIDAYS: tuple[tuple[int, int, str, str, str], ...] = (
    (1, 1, "nowruz_1", "Nowruz", "نوروز"),
    (1, 2, "nowruz_2", "Nowruz", "نوروز"),
    (1, 3, "nowruz_3", "Nowruz", "نوروز"),
    (1, 4, "nowruz_4", "Nowruz", "نوروز"),
    (1, 12, "islamic_republic_day", "Islamic Republic Day", "روز جمهوری اسلامی"),
    (1, 13, "sizdah_bedar", "Sizdah Bedar (Nature Day)", "سیزده بدر"),
    (3, 14, "khomeini_passing", "Passing of Imam Khomeini", "رحلت امام خمینی"),
    (3, 15, "khordad_uprising", "Khordad Uprising", "قیام ۱۵ خرداد"),
    (11, 22, "revolution_victory", "Victory of the Islamic Revolution", "پیروزی انقلاب اسلامی"),
    (12, 29, "oil_nationalisation", "Nationalisation of the Oil Industry", "ملی شدن صنعت نفت"),
)

# Lunar holidays: fixed Hijri (month, day). Hijri months here are
# 1 Muharram, 2 Safar, 3 Rabi I, 4 Rabi II, 5 Jumada I, 6 Jumada II,
# 7 Rajab, 8 Sha'ban, 9 Ramadan, 10 Shawwal, 11 Dhu al-Qi'dah, 12 Dhu al-Hijjah.
#
# Every one of these was confirmed present in the Jalali 1403-1405 rows this
# module replaces, by converting those rows back to Hijri; see the module
# docstring for how far the arithmetic date sat from the observed one.
LUNAR_HOLIDAYS: tuple[tuple[int, int, str, str, str], ...] = (
    (1, 9, "tasua", "Tasua", "تاسوعای حسینی"),
    (1, 10, "ashura", "Ashura", "عاشورای حسینی"),
    (2, 20, "arbaeen", "Arbaeen", "اربعین حسینی"),
    (2, 28, "prophet_passing", "Passing of the Prophet / Martyrdom of Imam Hasan",
     "رحلت پیامبر و شهادت امام حسن مجتبی"),
    # 30 Safar in the observed calendar. The tabular calendar always gives Safar
    # 29 days, so this one is clamped to the last day of Safar by
    # _resolve_lunar_day below -- one of the places the computed date is most
    # likely to need an override.
    (2, 30, "imam_reza_martyrdom", "Martyrdom of Imam Reza", "شهادت امام رضا"),
    (3, 8, "askari_martyrdom", "Martyrdom of Imam Hasan al-Askari", "شهادت امام حسن عسکری"),
    (3, 17, "prophet_birth", "Birth of the Prophet / Imam Sadiq",
     "میلاد پیامبر و امام جعفر صادق"),
    (6, 3, "fatimah_martyrdom", "Martyrdom of Fatimah", "شهادت حضرت فاطمه"),
    (7, 13, "imam_ali_birth", "Birth of Imam Ali", "ولادت امام علی"),
    (7, 27, "mabas", "Mab'as (Prophet's Mission)", "مبعث"),
    (8, 15, "mahdi_birth", "Birth of Imam Mahdi (Nimeye Sha'ban)", "نیمه شعبان"),
    (9, 21, "imam_ali_martyrdom", "Martyrdom of Imam Ali", "شهادت امام علی"),
    (10, 1, "eid_fitr", "Eid al-Fitr", "عید فطر"),
    (10, 2, "eid_fitr_2", "Eid al-Fitr (second day)", "تعطیل پس از عید فطر"),
    (10, 25, "sadiq_martyrdom", "Martyrdom of Imam Ja'far Sadiq", "شهادت امام جعفر صادق"),
    (12, 7, "baqir_martyrdom", "Martyrdom of Imam Baqir", "شهادت امام محمد باقر"),
    (12, 10, "eid_qorban", "Eid al-Qorban (al-Adha)", "عید قربان"),
    (12, 18, "eid_ghadir", "Eid al-Ghadir", "عید غدیر خم"),
)

LUNAR_KEYS = frozenset(key for _m, _d, key, _en, _fa in LUNAR_HOLIDAYS)
SOLAR_KEYS = frozenset(key for _m, _d, key, _en, _fa in SOLAR_HOLIDAYS)


def _resolve_lunar_day(hy: int, hm: int, hd: int) -> int:
    """Clamp a nominal Hijri day onto a month the tabular calendar actually has.

    Iran's calendar names a holiday "30 Safar" even though Safar only sometimes
    has thirty days; the arithmetic calendar gives it 29 every year. Clamping to
    the last day of the month keeps the holiday in the right week rather than
    dropping it or spilling it into the next month.
    """
    return min(hd, hijri_month_length(hy, hm))


def jalali_year_gregorian_span(jy: int) -> tuple[datetime.date, datetime.date]:
    """Gregorian half-open interval [start, end) covering Jalali year ``jy``.

    Uses the project's own converter so this module can never disagree with the
    rest of the app about where a Jalali year begins.
    """
    start = datetime.date(*jalali_to_gregorian(jy, 1, 1))
    end = datetime.date(*jalali_to_gregorian(jy + 1, 1, 1))
    return start, end


@lru_cache(maxsize=64)
def generate_year(jy: int) -> tuple[Holiday, ...]:
    """Every solar + computed-lunar holiday falling inside Jalali year ``jy``.

    Cached because the shift pages ask about a holiday once per calendar day per
    person; generating a year costs a few hundred integer operations, and doing
    that thirty times per page render for the same year is pure waste. The
    result is an immutable tuple, so a caller cannot corrupt the cached value
    for everyone else, and the cache is bounded because ``jy`` can come from a
    hand-typed /shift/<year>/ URL and must not be allowed to grow without limit.

    Lunar entries here are always ``confirmed=False``. Applying official
    announcements on top is :mod:`people.iran_holidays`'s job, not ours.
    """
    out: list[Holiday] = []
    for jm, jd, key, en, fa in SOLAR_HOLIDAYS:
        out.append(Holiday(jm, jd, key, en, fa, lunar=False, confirmed=True))

    try:
        span_start, span_end = jalali_year_gregorian_span(jy)
    except (ValueError, OverflowError):
        # A Jalali year outside the Gregorian range datetime can express (a
        # hand-typed URL, or a corrupt record). The solar holidays are still
        # perfectly well defined, so return those rather than raising into a
        # page render; shift_hours drops such days anyway.
        return tuple(sorted(out))

    # A 365-day solar year overlaps at most three Hijri years at the edges, so
    # scan a small window around the Hijri year the Nowruz falls in.
    hy_start = jdn_to_hijri(gregorian_to_jdn(span_start))[0]
    for hy in range(hy_start - 1, hy_start + 3):
        for hm, hd, key, en, fa in LUNAR_HOLIDAYS:
            day = _resolve_lunar_day(hy, hm, hd)
            when = hijri_to_gregorian(hy, hm, day)
            if not (span_start <= when < span_end):
                continue
            _jy, jm, jd = gregorian_to_jalali(when.year, when.month, when.day)
            out.append(Holiday(jm, jd, key, en, fa, lunar=True, confirmed=False))

    return tuple(sorted(out))
