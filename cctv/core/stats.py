"""System statistics (CPU, RAM, network) without any third-party dependency.

Reads Linux ``/proc`` directly so the app works on any Ubuntu/Debian machine
without installing psutil.  GPU decode status is aggregated separately from
the stream threads (see :mod:`cctv.core.streamer`).
"""
from __future__ import annotations

import time


def _read_cpu() -> tuple[int, int]:
    with open("/proc/stat", encoding="utf-8") as handle:
        parts = handle.readline().split()
    # user nice system idle iowait irq softirq steal guest guest_nice
    idle = int(parts[4]) + int(parts[5])
    total = sum(int(value) for value in parts[1:])
    return total, idle


def _read_mem() -> tuple[int, int]:
    total = available = 0
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("MemTotal:"):
                total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                available = int(line.split()[1])
            if total and available:
                break
    return total, available


def _read_net() -> tuple[int, int]:
    rx_bytes = tx_bytes = 0
    with open("/proc/net/dev", encoding="utf-8") as handle:
        handle.readline()  # header
        handle.readline()
        for line in handle:
            if ":" not in line:
                continue
            name, data = line.split(":", 1)
            if name.strip() == "lo":
                continue
            fields = data.split()
            try:
                rx_bytes += int(fields[0])
                tx_bytes += int(fields[8])
            except (IndexError, ValueError):
                continue
    return rx_bytes, tx_bytes


class SystemStats:
    """Samples CPU / RAM / network usage; ``snapshot()`` is cheap."""

    def __init__(self) -> None:
        self._cpu_total, self._cpu_idle = _read_cpu()
        self._net_rx, self._net_tx = _read_net()
        self._last_time = time.monotonic()
        self._last = {"cpu_pct": 0.0, "mem_pct": 0.0,
                      "net_down_bps": 0.0, "net_up_bps": 0.0}

    def snapshot(self) -> dict:
        try:
            cpu_total, cpu_idle = _read_cpu()
            mem_total, mem_avail = _read_mem()
            net_rx, net_tx = _read_net()
        except OSError:
            return self._last

        now = time.monotonic()
        dt = max(0.05, now - self._last_time)
        self._last_time = now

        if cpu_total > self._cpu_total:
            used = (cpu_total - cpu_idle) - (self._cpu_total - self._cpu_idle)
            total = cpu_total - self._cpu_total
            cpu_pct = 100.0 * used / max(1, total)
        else:
            cpu_pct = 0.0
        self._cpu_total, self._cpu_idle = cpu_total, cpu_idle

        mem_pct = 0.0
        if mem_total:
            mem_pct = 100.0 * (mem_total - mem_avail) / mem_total

        net_down = max(0.0, (net_rx - self._net_rx) * 8.0 / dt)
        net_up = max(0.0, (net_tx - self._net_tx) * 8.0 / dt)
        self._net_rx, self._net_tx = net_rx, net_tx

        self._last = {"cpu_pct": cpu_pct, "mem_pct": mem_pct,
                      "net_down_bps": net_down, "net_up_bps": net_up}
        return self._last


def format_bps(bps: float) -> str:
    """Format a bit rate for the status bar, e.g. 1.2M / 340K / 12K."""
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f}M"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f}K"
    return f"{bps:.0f}"
