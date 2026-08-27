from kot.system_stats import calculate_cpu_percent, parse_cpu_totals, parse_ram_percent


def test_cpu_percent_uses_delta_between_samples() -> None:
    previous = parse_cpu_totals("cpu  100 0 50 850 0 0 0 0\n")
    current = parse_cpu_totals("cpu  130 0 60 910 0 0 0 0\n")

    assert calculate_cpu_percent(previous, current) == 40


def test_ram_percent_uses_mem_available() -> None:
    text = "MemTotal:       1000 kB\nMemAvailable:    250 kB\n"

    assert parse_ram_percent(text) == 75
