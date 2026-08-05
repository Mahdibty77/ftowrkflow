"""Minimal Gregorian -> Jalali (Persian/Solar Hijri) date conversion.

Implemented with no third-party dependency so the platform installs cleanly on
any server. Only the conversion we need (today's Jalali year + month) is
provided. Algorithm adapted from the well-known Birashk / jalaali method.
"""
from __future__ import annotations

import datetime


_BREAKS = [
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
    1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178,
]


def _div(a: int, b: int) -> int:
    return a // b


def _jal_cal(jy: int):
    bl = len(_BREAKS)
    gy = jy + 621
    leap_j = -14
    jp = _BREAKS[0]
    jump = 0
    for i in range(1, bl):
        jm = _BREAKS[i]
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(jump % 33, 4)
        jp = jm
    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div((n % 33) + 3, 4)
    if (jump % 33) == 4 and (jump - n) == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    return leap_j, gy, march


def gregorian_to_jalali(g_y: int, g_m: int, g_d: int):
    """Return (jalali_year, jalali_month, jalali_day)."""
    gdm = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = g_y - 1600
    gm2 = g_m - 1
    gd2 = g_d - 1
    g_day_no = 365 * gy2 + _div(gy2 + 3, 4) - _div(gy2 + 99, 100) + _div(gy2 + 399, 400)
    g_day_no += gdm[gm2] + gd2
    if gm2 > 1 and ((g_y % 4 == 0 and g_y % 100 != 0) or (g_y % 400 == 0)):
        g_day_no += 1

    j_day_no = g_day_no - 79
    j_np = _div(j_day_no, 12053)
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * _div(j_day_no, 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += _div(j_day_no - 1, 365)
        j_day_no = (j_day_no - 1) % 365
    if j_day_no < 186:
        jm = 1 + _div(j_day_no, 31)
        jd = 1 + (j_day_no % 31)
    else:
        jm = 7 + _div(j_day_no - 186, 30)
        jd = 1 + ((j_day_no - 186) % 30)
    return jy, jm, jd


def jalali_year_month(when: datetime.date | None = None) -> tuple[int, int]:
    """Return the current Jalali (year, month)."""
    when = when or datetime.date.today()
    jy, jm, _jd = gregorian_to_jalali(when.year, when.month, when.day)
    return jy, jm


def jalali_to_gregorian(jy: int, jm: int, jd: int):
    """Inverse of :func:`gregorian_to_jalali` (jalaali method).

    Return (gregorian_year, gregorian_month, gregorian_day).
    """
    jy2 = jy - 979
    j_day_no = 365 * jy2 + _div(jy2, 33) * 8 + _div((jy2 % 33) + 3, 4)
    if jm < 7:
        j_day_no += (jm - 1) * 31
    else:
        j_day_no += (jm - 7) * 30 + 186
    j_day_no += jd - 1

    g_day_no = j_day_no + 79
    gy = 1600 + 400 * _div(g_day_no, 146097)
    g_day_no %= 146097
    leap = True
    if g_day_no >= 36525:
        g_day_no -= 1
        gy += 100 * _div(g_day_no, 36524)
        g_day_no %= 36524
        if g_day_no >= 365:
            g_day_no += 1
        else:
            leap = False
    gy += 4 * _div(g_day_no, 1461)
    g_day_no %= 1461
    if g_day_no >= 366:
        leap = False
        g_day_no -= 1
        gy += _div(g_day_no, 365)
        g_day_no %= 365
    gd = g_day_no + 1
    months = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    for i in range(1, 13):
        if gd <= months[i]:
            gm = i
            break
        gd -= months[i]
    return gy, gm, gd
