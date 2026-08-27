from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SystemSnapshot:
    cpu_percent: int | None
    ram_percent: int | None


def parse_cpu_totals(text: str) -> tuple[int, int]:
    fields = text.splitlines()[0].split()
    if not fields or fields[0] != "cpu" or len(fields) < 5:
        raise ValueError("invalid /proc/stat CPU line")

    values = [int(value) for value in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def calculate_cpu_percent(previous: tuple[int, int], current: tuple[int, int]) -> int | None:
    total_delta = current[0] - previous[0]
    idle_delta = current[1] - previous[1]
    if total_delta <= 0:
        return None
    busy = max(0, min(total_delta, total_delta - idle_delta))
    return round(busy * 100 / total_delta)


def parse_ram_percent(text: str) -> int:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        token = remainder.strip().split(maxsplit=1)[0]
        if token.isdigit():
            values[key] = int(token)

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if total <= 0:
        raise ValueError("MemTotal is missing from /proc/meminfo")
    used = max(0, min(total, total - available))
    return round(used * 100 / total)


class SystemMonitor:
    def __init__(
        self,
        stat_path: Path = Path("/proc/stat"),
        meminfo_path: Path = Path("/proc/meminfo"),
    ) -> None:
        self.stat_path = stat_path
        self.meminfo_path = meminfo_path
        self._previous_cpu: tuple[int, int] | None = None

    def sample(self) -> SystemSnapshot:
        current_cpu = parse_cpu_totals(self.stat_path.read_text())
        cpu_percent = (
            None
            if self._previous_cpu is None
            else calculate_cpu_percent(self._previous_cpu, current_cpu)
        )
        self._previous_cpu = current_cpu
        ram_percent = parse_ram_percent(self.meminfo_path.read_text())
        return SystemSnapshot(cpu_percent=cpu_percent, ram_percent=ram_percent)
