"""Iran official public holidays on the Jalali (Solar Hijri) calendar.

Weekend days used by work-shift planning are Thursday and Friday (Python
weekday 3 and 4). Official holidays are layered on top; a holiday that falls on
a weekend is not double-counted as an extra day off.

This module used to *be* the calendar: a hand-typed table of (month, day) pairs
per Jalali year. That table was partial for 1406 and empty from 1407 onward, and
because a missing holiday reads as an ordinary working day, every year nobody
had got round to typing quietly inflated planned hours for every employee. The
calendar is now generated instead -- see :mod:`people.calendar_ir` -- so it is
complete for every future year with no annual edit.

What is left here is the part that genuinely cannot be computed: Iran fixes its
lunar holidays by moon sighting and announces them days ahead, so a computed
lunar date may be a day or two off what the country actually observes. The two
override tables below are where an official announcement is recorded, and they
always beat the computed date. The rows already confirmed when the calendar was
generated (Jalali 1403-1406) were carried straight into them, so no year that
was right before this change became wrong.

Measured against the table this replaced: not one date it held has been lost.
1403, 1404 and 1405 come out to exactly the same set of dates as before, so the
payroll months already settled under the old table are untouched. 1406 is the
one year that deliberately does not match: the old row held four dates and was
labelled PARTIAL in its own comment -- it was the owner's record of four days
off, not a claim that 1406 has only four -- so all four are still here and the
fourteen the old table never got round to typing are now generated as well.
Matching 1406 exactly would mean keeping ten-odd public holidays planned as
ordinary working days, which is the bug this module was rewritten to remove.

>>> ADDING NEXT YEAR'S OFFICIAL CALENDAR: put it in OFFICIAL_YEAR_CALENDARS if
>>> you have the whole published list, or in OFFICIAL_LUNAR_DATES if you only
>>> have a single announcement (e.g. "Eid al-Fitr is confirmed for 20 Farvardin").
>>> If you know a day is a holiday but not *which* holiday, put it in
>>> OFFICIAL_EXTRA_DATES -- never guess a key, because a key you guess wrong
>>> silently moves the holiday it names away from its real date.
>>> You never have to add anything for the calendar to keep working -- overrides
>>> only sharpen dates that are already generated.
"""

from __future__ import annotations

from functools import lru_cache

from .calendar_ir import (
    LUNAR_HOLIDAYS,
    LUNAR_KEYS,
    SOLAR_HOLIDAYS,
    Holiday,
    generate_year,
)

# Python weekday: Mon=0 … Sun=6. Iran office weekend for this app:
WEEKEND_WEEKDAYS = frozenset({3, 4})  # Thursday, Friday

# National / solar holidays that fall on the same Jalali date every year.
# Derived from the single definition in calendar_ir so the two can never drift
# apart; the contents are byte-for-byte the ten pairs this module carried
# before the calendar was generated.
FIXED_HOLIDAYS: frozenset[tuple[int, int]] = frozenset(
    (jm, jd) for jm, jd, _key, _en, _fa in SOLAR_HOLIDAYS
)


# ---------------------------------------------------------------------------
# Override table 1: whole published years
# ---------------------------------------------------------------------------
#
# A year in here has had its official calendar published in full, so we use that
# list verbatim as the year's variable (lunar + one-off) holidays and generate
# nothing for it. That is what makes this change safe: 1403, 1404 and 1405 are
# already past or in progress, planned hours for those months are settled, and
# replacing the whole year means those months come out to exactly the same
# numbers as before.
#
# These lists also contain days that are not calendar holidays at all and could
# never be computed -- the nationwide closures the government declared at short
# notice (Tir 14-15 in 1405, Tir 4 in 1404, and so on). Another reason a
# published year is used as-is rather than merged with generated dates.
#
# Note for whoever maintains this: the published rows are the ground truth and
# are reproduced untouched, including their gaps. 1403 and 1404 have no Eid
# al-Qorban entry and 1404's Ramadan date sits earlier than the martyrdom of
# Imam Ali normally falls. Those look like holes in the original source, but
# correcting them would change settled payroll months, so they stay.
OFFICIAL_YEAR_CALENDARS: dict[int, frozenset[tuple[int, int]]] = {
    1403: frozenset({
        (1, 22), (2, 15), (4, 5), (4, 15), (4, 25), (4, 26),
        (6, 4), (6, 12), (6, 14), (6, 22), (7, 2),
        (9, 15), (10, 25), (11, 9),
    }),
    1404: frozenset({
        (1, 11), (1, 13), (2, 4), (3, 24), (4, 4), (4, 14), (4, 15),
        (5, 23), (5, 28), (5, 31), (6, 2),
        (8, 23), (10, 13), (10, 27), (12, 9),
    }),
    # 1405 (timestamp.ir holidays list). Nationwide specials Tir 14–15 included;
    # Tehran-only Tir 13/16 omitted.
    1405: frozenset({
        (1, 24),                         # شهادت امام جعفر صادق / عید فطر
        (3, 3), (3, 6),                  # شهادت باقر / عید قربان
        (4, 3), (4, 4),                  # تاسوعا / عاشورا
        (4, 14), (4, 15),                # تعطیلی سراسری
        (5, 13), (5, 21), (5, 22), (5, 30),  # اربعین / رحلت / رضا / عسکری
        (6, 8),                          # ولادت پیامبر و امام صادق
        (8, 22),                         # شهادت حضرت فاطمه
        (10, 2), (10, 16),               # ولادت علی / مبعث
        (11, 4),                         # نیمه شعبان
        (12, 9), (12, 19), (12, 20),     # شهادت علی / عید فطر
    }),
}


# ---------------------------------------------------------------------------
# Override table 2: individual confirmed announcements
# ---------------------------------------------------------------------------
#
# For a year whose calendar has NOT been published in full, every generated
# lunar date stands except the ones named here. Keying by holiday rather than by
# date is deliberate: a confirmed date *moves* the computed date of that holiday
# instead of sitting next to it, so a one-day announcement shift cannot turn one
# day off into two.
#
# ONE ANNOUNCEMENT MOVES ONE OCCURRENCE. That is the part it is easy to get
# wrong. A Jalali year is roughly eleven days longer than a lunar year, so the
# same Hijri date can occur TWICE inside it -- once in the opening days of
# Farvardin and again in the closing days of Esfand, about eleven days before
# the next Nowruz. generate_year emits both, correctly: they are two separate
# public holidays. Jalali 1413 has Tasua and Ashura twice, 1411 has Eid
# al-Qorban and Eid al-Ghadir twice, and 16 of the 38 years 1403-1440 have at
# least one such pair. An override that replaced *every* record carrying the
# key would collapse both occurrences onto the announced day and one real
# holiday would simply vanish -- becoming a planned working day, which is the
# exact failure this module exists to prevent. (An earlier version of this
# comment had the risk backwards: the danger of key-based overrides is not one
# day off turning into two, it is two turning into one.)
#
# So an announcement is matched to the occurrence it is NEAREST to, counted in
# days inside the Jalali year. That keeps the table format as it is -- a plain
# {key: (month, day)} -- and it is unambiguous in practice, because an
# announcement shifts a holiday by a day or two while the two occurrences of one
# holiday sit about 354 days apart. Each override therefore moves exactly one
# record and drops none, so recording an announcement can never reduce the
# number of days off in a year.
#
# The keys are the holiday keys in calendar_ir.LUNAR_HOLIDAYS; a typo raises at
# import (see the check below) rather than silently doing nothing.
#
# 1406 came from the old hardcoded table, which held four dates and nothing
# else. Converting them back to Hijri identified three of them exactly --
# 20 Safar, 28 Safar and 8 Rabi I. The fourth, 17 Mordad, is 6 Rabi I, which is
# no holiday in any list we have. It used to be recorded here as the martyrdom
# of Imam Reza, on the grounds that it fell in about the right place; that was a
# guess, and a guess in this table is expensive, because the override would move
# the computed 30-Safar date (11 Mordad 1406) onto 17 Mordad and the real
# 30-Safar day off would be gone. The day is kept -- the old table is the
# owner's record of a day this business did not work -- but it now sits in
# OFFICIAL_EXTRA_DATES with no holiday attached, where it can only add a day off
# and not take one away. Give it a name when the published 1406 calendar says
# what it is.
OFFICIAL_LUNAR_DATES: dict[int, dict[str, tuple[int, int]]] = {
    1406: {
        "arbaeen": (5, 2),
        "prophet_passing": (5, 10),
        "askari_martyrdom": (5, 19),
    },
}


# ---------------------------------------------------------------------------
# Override table 3: confirmed days off with no identified holiday
# ---------------------------------------------------------------------------
#
# A date we know the country did not work but cannot attribute to any holiday in
# calendar_ir.LUNAR_HOLIDAYS: an unidentified row inherited from an older table,
# or a one-off government closure announced for a year that is otherwise not
# published. Entries here are ADDED to the generated calendar and substituted
# for nothing, so recording one can only ever add a day off.
#
# Use this instead of guessing a key in OFFICIAL_LUNAR_DATES. A guessed key is
# not a harmless mislabel: it moves the named holiday off its computed date, so
# a wrong guess deletes a real day off somewhere else in the year.
OFFICIAL_EXTRA_DATES: dict[int, frozenset[tuple[int, int]]] = {
    # 6 Rabi I 1449 = 17 Mordad 1406. A day off in the old hardcoded table that
    # matches no holiday we can name; see the 1406 note above.
    1406: frozenset({(5, 17)}),
}


# A mistyped key would be a silent no-op -- the holiday would keep its computed
# date and nobody would notice until someone was marked absent on a public
# holiday. Fail loudly at import instead, where a deploy or a test run catches
# it immediately.
for _jy, _named in OFFICIAL_LUNAR_DATES.items():
    for _key in _named:
        if _key not in LUNAR_KEYS:
            raise ValueError(
                f"OFFICIAL_LUNAR_DATES[{_jy}] names unknown holiday {_key!r}; "
                f"it must be one of calendar_ir.LUNAR_HOLIDAYS."
            )

# Same reasoning for the other silent no-op: a published year is used verbatim,
# so a per-holiday or extra date recorded for a year that is also in
# OFFICIAL_YEAR_CALENDARS would be read by nobody. Say so at import instead of
# leaving the next maintainer wondering why their announcement did nothing.
for _jy in set(OFFICIAL_LUNAR_DATES) | set(OFFICIAL_EXTRA_DATES):
    if _jy in OFFICIAL_YEAR_CALENDARS:
        raise ValueError(
            f"Jalali {_jy} is in OFFICIAL_YEAR_CALENDARS, which is used verbatim; "
            f"its OFFICIAL_LUNAR_DATES / OFFICIAL_EXTRA_DATES rows would be ignored. "
            f"Fold the announcement into the published list instead."
        )


# Backwards-compatible alias. Older code and shell one-liners referred to the
# per-year table by this name; it still means "the variable holidays we were
# told about for this year", it is simply no longer the only source.
YEARLY_HOLIDAYS = OFFICIAL_YEAR_CALENDARS

# Lookup used to name an announcement that the arithmetic calendar happened to
# place outside the Jalali year being asked about.
_LUNAR_BY_KEY = {row[2]: row for row in LUNAR_HOLIDAYS}


def _jalali_day_of_year(jm: int, jd: int) -> int:
    """Ordinal of a Jalali (month, day) inside its year, 1 Farvardin == 1.

    Only ever used to compare two dates from the same year, which is why it can
    be this crude: Farvardin-Shahrivar are 31 days and Mehr-Bahman 30, and
    Esfand's length never enters the sum because nothing is counted past it. It
    deliberately does no validation and cannot raise -- it is fed dates typed
    into the override tables by hand, and a shift page must not 500 because a
    month number is out of range.
    """
    return (jm - 1) * 31 + jd if jm <= 6 else 186 + (jm - 7) * 30 + jd


@lru_cache(maxsize=64)
def official_holidays(jy: int) -> tuple[Holiday, ...]:
    """Every official holiday in Jalali year ``jy``, overrides already applied.

    Each entry carries ``confirmed``: True for solar holidays and for lunar ones
    taken from an official announcement, False for a lunar date we computed and
    Iran has not yet fixed. Anything showing these to a user should say so.

    Cached (bounded, because ``jy`` can arrive from a hand-typed URL) and
    returning an immutable tuple, so repeated calls on a shift page are free and
    always identical.
    """
    generated = generate_year(jy)
    named = OFFICIAL_LUNAR_DATES.get(jy, {})
    published = OFFICIAL_YEAR_CALENDARS.get(jy)

    out: list[Holiday] = [h for h in generated if not h.lunar]

    if published is not None:
        # Whole year published: the announced list is the year's variable
        # holidays, full stop. We keep no computed lunar date for such a year,
        # because the published list also contains one-off government closures
        # that no calendar could produce, and mixing the two would double up
        # every holiday the arithmetic put a day out.
        for jm, jd in sorted(published):
            out.append(Holiday(
                jm, jd,
                key=f"announced_{jm}_{jd}",
                name_en="Official holiday (announced)",
                name_fa="تعطیل رسمی",
                lunar=True,
                confirmed=True,
            ))
        return tuple(sorted(out))

    computed = [h for h in generated if h.lunar]

    # Decide, per announcement, which single generated record it moves. A key
    # can appear twice in one Jalali year (see the note above OFFICIAL_LUNAR_
    # DATES), and moving both would delete a real day off, so each announcement
    # claims the occurrence nearest to the date it names and leaves any other
    # occurrence on its computed date.
    moved: dict[str, int] = {}
    for key, (jm, jd) in named.items():
        target = _jalali_day_of_year(jm, jd)
        candidates = [i for i, h in enumerate(computed) if h.key == key]
        if not candidates:
            continue
        # Ties break on the earlier occurrence purely so the result is stable;
        # a tie needs the two occurrences to be equidistant, which cannot happen
        # while they are most of a lunar year apart.
        moved[key] = min(
            candidates,
            key=lambda i: (
                abs(_jalali_day_of_year(computed[i].jmonth, computed[i].jday) - target),
                i,
            ),
        )

    for i, h in enumerate(computed):
        if moved.get(h.key) == i:
            jm, jd = named[h.key]
            out.append(h._replace(jmonth=jm, jday=jd, confirmed=True))
        else:
            out.append(h)

    # An announcement can name a holiday the arithmetic calendar placed just
    # outside this Jalali year (Nowruz can fall between the computed and the
    # announced date). Those matched no occurrence above and would otherwise be
    # dropped, so add them back.
    for key, (jm, jd) in sorted(named.items()):
        if key in moved:
            continue
        # The key is guaranteed to exist -- the import-time check above rejects
        # any table entry that does not name a real holiday.
        _hm, _hd, _key, name_en, name_fa = _LUNAR_BY_KEY[key]
        out.append(Holiday(jm, jd, key, name_en, name_fa, lunar=True, confirmed=True))

    # Days off we know about but cannot attribute. Added, never substituted. A
    # date that some holiday already occupies is skipped rather than doubled up:
    # the day is a day off either way, and a second record on it would only make
    # a holiday list read as though two things happened.
    taken = {(h.jmonth, h.jday) for h in out}
    for jm, jd in sorted(OFFICIAL_EXTRA_DATES.get(jy, frozenset())):
        if (jm, jd) in taken:
            continue
        taken.add((jm, jd))
        out.append(Holiday(
            jm, jd,
            key=f"announced_{jm}_{jd}",
            name_en="Official holiday (announced)",
            name_fa="تعطیل رسمی",
            lunar=True,
            confirmed=True,
        ))

    return tuple(sorted(out))


@lru_cache(maxsize=64)
def _official_md_frozenset(jy: int) -> frozenset[tuple[int, int]]:
    """Cached, immutable (month, day) set -- the shape the hot path wants."""
    return frozenset((h.jmonth, h.jday) for h in official_holidays(jy))


def official_holiday_md_set(jy: int) -> set[tuple[int, int]]:
    """All official (month, day) pairs for Jalali year ``jy``.

    Returns a fresh mutable set, as it always has, so callers that modify the
    result cannot poison the cache behind it.
    """
    return set(_official_md_frozenset(jy))


def is_official_holiday(jy: int, jm: int, jd: int) -> bool:
    # Straight to the cached frozenset: this is called once per day per person
    # on the shift pages, and building a new set each time was pure waste.
    return (jm, jd) in _official_md_frozenset(jy)
