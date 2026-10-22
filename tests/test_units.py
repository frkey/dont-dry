from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from dont_dry import page, pwg
from dont_dry.schedule import Cron, CronError

SP = ZoneInfo("America/Sao_Paulo")


# -- PWG raster ---------------------------------------------------------------


@pytest.mark.parametrize("doc_type", ["srgb_8", "sgray_8"])
def test_pwg_roundtrip_test_page(doc_type):
    image = page.render(dpi=100)
    data = pwg.encode(image, dpi=100, doc_type=doc_type)
    (decoded,) = pwg.decode(data)
    mode = pwg.DOCUMENT_TYPES[doc_type][0]
    assert decoded.pixels == image.convert(mode).tobytes()
    assert (decoded.width, decoded.height) == image.size


def test_pwg_roundtrip_noise_and_edge_cases():
    import random
    rnd = random.Random(1)
    image = Image.new("RGB", (301, 7))
    pixels = []
    for y in range(7):
        for x in range(301):
            if y == 0:
                pixels.append((rnd.randrange(256),) * 3)          # all literals
            elif y in (1, 2):
                pixels.append((10, 20, 30))                        # long runs, repeated row
            else:
                pixels.append((x // 3 % 256, 0, 0))                # runs of 3
    image.putdata(pixels)
    (decoded,) = pwg.decode(pwg.encode(image, dpi=300))
    assert decoded.pixels == image.tobytes()


def test_pwg_header_fields():
    data = pwg.encode(Image.new("RGB", (2480, 3508), "white"), dpi=300)
    assert data[:4] == b"RaS2"
    assert len(data) > 4 + pwg.HEADER_SIZE
    (decoded,) = pwg.decode(data)
    assert decoded.bits_per_pixel == 24


# -- cron -----------------------------------------------------------------------


def test_cron_default_schedule():
    cron = Cron("0 9 * * 1,4")
    start = datetime(2026, 10, 21, 20, 0, tzinfo=SP)  # a Wednesday
    first = cron.next_after(start)
    second = cron.next_after(first)
    assert first == datetime(2026, 10, 22, 9, 0, tzinfo=SP)   # Thursday
    assert second == datetime(2026, 10, 26, 9, 0, tzinfo=SP)  # Monday


@pytest.mark.parametrize("expr,start,expected", [
    ("*/15 * * * *", datetime(2026, 1, 1, 10, 7), datetime(2026, 1, 1, 10, 15)),
    ("30 8 * * mon-fri", datetime(2026, 10, 24, 12, 0), datetime(2026, 10, 26, 8, 30)),
    ("0 12 1 * *", datetime(2026, 10, 2, 0, 0), datetime(2026, 11, 1, 12, 0)),
    ("0 0 * * 7", datetime(2026, 10, 21, 0, 0), datetime(2026, 10, 25, 0, 0)),
    ("0 6 13 * fri", datetime(2026, 2, 1, 0, 0), datetime(2026, 2, 6, 6, 0)),  # dom OR dow
    ("@weekly", datetime(2026, 10, 21, 0, 0), datetime(2026, 10, 25, 0, 0)),
    ("0 9 29 feb *", datetime(2026, 3, 1), datetime(2028, 2, 29, 9, 0)),
    # a starred day field (also "*/2") narrows the other one instead of adding to it:
    # odd days that are Mondays, as in Vixie cron and cronie
    ("0 0 */2 * mon", datetime(2026, 10, 21, 0, 0), datetime(2026, 11, 9, 0, 0)),
    ("0 0 1 * */2", datetime(2026, 10, 21, 0, 0), datetime(2026, 11, 1, 0, 0)),  # a Sunday
])
def test_cron_next(expr, start, expected):
    assert Cron(expr).next_after(start) == expected


@pytest.mark.parametrize("expr", ["", "* * * *", "61 * * * *", "* * * * mon-xyz",
                                  "*/0 * * * *", "5-1 * * * *", "0 0 31 2 *"])
def test_cron_invalid(expr):
    with pytest.raises(CronError):
        Cron(expr).next_after(datetime(2026, 1, 1))


# -- cron across daylight saving time --------------------------------------------

BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")


@pytest.mark.parametrize("zone,day,expr", [
    (BERLIN, datetime(2026, 10, 25), "30 2 * * *"),    # 02:00-03:00 happens twice
    (BERLIN, datetime(2026, 10, 25), "*/20 * * * *"),
    (BERLIN, datetime(2026, 3, 29), "30 2 * * *"),     # 02:00-03:00 does not happen
    (NEW_YORK, datetime(2026, 11, 1), "30 1 * * *"),
    (NEW_YORK, datetime(2026, 3, 8), "30 2 * * *"),
])
def test_cron_never_goes_back_or_repeats_across_dst(zone, day, expr):
    cron = Cron(expr)
    start = datetime(day.year, day.month, day.day, tzinfo=zone).timestamp() - 3600
    seen = []
    for minute in range(26 * 60):  # every instant, minute by minute, around the change
        now = datetime.fromtimestamp(start + minute * 60, zone)
        nxt = cron.next_after(now)
        assert nxt.timestamp() > now.timestamp(), (now, nxt)
        if not seen or seen[-1] != nxt.timestamp():
            seen.append(nxt.timestamp())
    walls = [datetime.fromtimestamp(t, zone).strftime("%m-%d %H:%M") for t in seen]
    assert len(walls) == len(set(walls)), walls  # each wall-clock time fires at most once


def test_cron_fires_the_first_of_two_identical_times():
    first = Cron("30 2 * * *").next_after(datetime(2026, 10, 25, 1, 0, tzinfo=BERLIN))
    assert first.utcoffset().total_seconds() == 7200            # 02:30 CEST, not CET
    after_it = datetime(2026, 10, 25, 2, 10, fold=1, tzinfo=BERLIN)   # 02:10 CET, later
    assert Cron("30 2 * * *").next_after(after_it).date() == datetime(2026, 10, 26).date()


def test_cron_moves_a_skipped_time_to_the_instant_it_maps_to():
    when = Cron("30 2 * * *").next_after(datetime(2026, 3, 29, 1, 0, tzinfo=BERLIN))
    assert (when.hour, when.minute, when.utcoffset().total_seconds()) == (3, 30, 7200)


# -- schedules that print too often ----------------------------------------------


@pytest.mark.parametrize("expr", ["* 9 * * 1", "*/15 * * * *", "0 * * * *", "0 9,10 * * *"])
def test_schedules_closer_than_the_minimum_gap_are_refused(expr):
    with pytest.raises(CronError, match="less than 3 hours apart"):
        Cron(expr).check(datetime(2026, 10, 21, 8, 0, tzinfo=SP))


@pytest.mark.parametrize("expr", ["0 9 * * 1,4", "0 */4 * * *", "0 9,21 * * *",
                                  "0 9 29 feb *", "@weekly"])
def test_reasonable_schedules_pass_the_check(expr):
    times = Cron(expr).check(datetime(2026, 10, 21, 8, 0, tzinfo=SP))
    assert times and all(b > a for a, b in zip(times, times[1:], strict=False))


def test_check_finds_schedules_that_never_fire():
    with pytest.raises(CronError, match="never fires"):
        Cron("0 9 30 2 *").check(datetime(2026, 10, 21, 8, 0, tzinfo=SP))


def test_check_measures_the_gap_on_the_wall_clock():
    # 02:00 does not exist on 2026-03-29 in Berlin (it prints at 03:00), yet the schedule is
    # three hours apart every day: it must not be refused depending on when dont-dry starts
    Cron("0 2,5 * * *").check(datetime(2026, 3, 20, 12, 0, tzinfo=BERLIN))


# -- the previous print, for catching up --------------------------------------------


@pytest.mark.parametrize("expr,now,expected", [
    ("0 9 * * 1,4", (2026, 10, 21, 20, 0), (2026, 10, 19, 9, 0)),
    ("0 9 * * 1,4", (2026, 10, 19, 9, 0), (2026, 10, 19, 9, 0)),   # "at or before"
    ("0 12 1 * *", (2026, 10, 2, 0, 0), (2026, 10, 1, 12, 0)),
    ("0 9 1 1 *", (2026, 10, 21, 0, 0), (2026, 1, 1, 9, 0)),        # needs the widest lookback
])
def test_cron_previous_print(expr, now, expected):
    found = Cron(expr).previous_at_or_before(datetime(*now, tzinfo=SP))
    assert found == datetime(*expected, tzinfo=SP)


def test_cron_previous_print_is_none_when_too_far_back():
    assert Cron("0 9 29 feb *").previous_at_or_before(datetime(2026, 10, 21, tzinfo=SP)) is None
