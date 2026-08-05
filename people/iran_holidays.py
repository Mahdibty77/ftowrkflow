"""Iran official public holidays on the Jalali (Solar Hijri) calendar.



Weekend days used by work-shift planning are Thursday and Friday

(Python weekday 3 and 4). Official holidays are layered on top; a holiday that

falls on a weekend is not double-counted as an extra day off.



Fixed solar holidays repeat every year. Lunar/variable holidays are listed per

Jalali year (extend the YEARLY map as new years are known).

"""

from __future__ import annotations



# Python weekday: Mon=0 … Sun=6. Iran office weekend for this app:

WEEKEND_WEEKDAYS = frozenset({3, 4})  # Thursday, Friday



# National / solar holidays that fall on the same Jalali date every year.

FIXED_HOLIDAYS: frozenset[tuple[int, int]] = frozenset({

    (1, 1), (1, 2), (1, 3), (1, 4),   # Nowruz

    (1, 12),                           # Islamic Republic Day

    (1, 13),                           # Nature Day (Sizdah Bedar)

    (3, 14), (3, 15),                  # Death of Khomeini / Revolt against Shah

    (11, 22),                          # Victory of Islamic Revolution

    (12, 29),                          # Nationalization of Oil (leap: also 30)

})



# Variable (mostly lunar) official holidays by Jalali year → {(month, day), …}

# Sources: timestamp.ir / official Iran calendar (extend as years roll forward).

YEARLY_HOLIDAYS: dict[int, frozenset[tuple[int, int]]] = {

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

    1406: frozenset({

        (5, 2), (5, 10), (5, 17), (5, 19),

    }),

}





def official_holiday_md_set(jy: int) -> set[tuple[int, int]]:

    """All official (month, day) pairs for Jalali year ``jy``."""

    out = set(FIXED_HOLIDAYS)

    out |= set(YEARLY_HOLIDAYS.get(jy, frozenset()))

    return out





def is_official_holiday(jy: int, jm: int, jd: int) -> bool:

    return (jm, jd) in official_holiday_md_set(jy)


