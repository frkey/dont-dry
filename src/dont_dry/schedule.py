"""Tiny cron expression parser (standard 5-field syntax)."""

from __future__ import annotations

from datetime import datetime, timedelta

DAY_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
MONTH_NAMES = {name: i + 1 for i, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

# (name, min, max, aliases)
FIELDS = [
    ("minute", 0, 59, {}),
    ("hour", 0, 23, {}),
    ("day of month", 1, 31, {}),
    ("month", 1, 12, MONTH_NAMES),
    ("day of week", 0, 7, DAY_NAMES),
]

MACROS = {
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@hourly": "0 * * * *",
}


class CronError(ValueError):
    pass


def _value(token: str, name: str, lo: int, hi: int, aliases: dict[str, int]) -> int:
    token = token.lower()
    if token in aliases:
        return aliases[token]
    try:
        value = int(token)
    except ValueError:
        raise CronError(f"invalid {name} value {token!r}") from None
    if not lo <= value <= hi:
        raise CronError(f"{name} value {value} out of range {lo}-{hi}")
    return value


def _parse_field(text: str, name: str, lo: int, hi: int, aliases: dict[str, int]) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            try:
                step = int(step_text)
            except ValueError:
                raise CronError(f"invalid step {step_text!r} in {name}") from None
            if step < 1:
                raise CronError(f"step must be positive in {name}")
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _value(a, name, lo, hi, aliases), _value(b, name, lo, hi, aliases)
            if start > end:
                raise CronError(f"invalid range {part!r} in {name}")
        else:
            start = _value(part, name, lo, hi, aliases)
            end = hi if step > 1 else start
        values.update(range(start, end + 1, step))
    return values


class Cron:
    def __init__(self, expression: str):
        self.expression = expression.strip()
        text = MACROS.get(self.expression.lower(), self.expression)
        parts = text.split()
        if len(parts) != 5:
            raise CronError(f"cron expression needs 5 fields, got {len(parts)}: {expression!r}")
        parsed = [_parse_field(p, *spec) for p, spec in zip(parts, FIELDS, strict=True)]
        self.minutes, self.hours, self.days, self.months, dows = parsed
        self.weekdays = {d % 7 for d in dows}  # 7 is also Sunday
        # As in Vixie cron, a field that starts with "*" (also "*/2") does not restrict the day
        # on its own: only when both day fields are restricted does either one match
        self.dom_star = parts[2].startswith("*")
        self.dow_star = parts[4].startswith("*")

    def _day_matches(self, dt: datetime) -> bool:
        if dt.month not in self.months:
            return False
        dom_ok = dt.day in self.days
        dow_ok = (dt.isoweekday() % 7) in self.weekdays
        if self.dom_star or self.dow_star:
            return dom_ok and dow_ok
        return dom_ok or dow_ok

    def next_after(self, after: datetime) -> datetime:
        """First matching minute strictly after `after` (keeps its tzinfo)."""
        dt = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = dt + timedelta(days=366 * 5)
        while dt < limit:
            if not self._day_matches(dt):
                dt = (dt + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if dt.hour not in self.hours:
                dt = (dt + timedelta(hours=1)).replace(minute=0)
                continue
            if dt.minute not in self.minutes:
                dt += timedelta(minutes=1)
                continue
            return dt
        raise CronError(f"cron expression never fires: {self.expression!r}")
