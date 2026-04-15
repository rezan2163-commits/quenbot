"""
QuenBot V2 — System Resource Monitor
======================================
Monitors CPU, RAM, disk usage and per-process resource consumption.
Works on Linux (production) with /proc fallback.
No external dependencies (uses stdlib only).
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("quenbot.resource_monitor")


@dataclass
class ResourceSnapshot:
    cpu_percent: float
    ram_total_mb: float
    ram_used_mb: float
    ram_percent: float
    disk_total_gb: float
    disk_used_gb: float
    disk_percent: float
    load_avg_1m: float
    load_avg_5m: float
    load_avg_15m: float
    process_rss_mb: float  # current python process RSS
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "ram_total_mb": round(self.ram_total_mb, 0),
            "ram_used_mb": round(self.ram_used_mb, 0),
            "ram_percent": round(self.ram_percent, 1),
            "ram_free_mb": round(self.ram_total_mb - self.ram_used_mb, 0),
            "disk_total_gb": round(self.disk_total_gb, 1),
            "disk_used_gb": round(self.disk_used_gb, 1),
            "disk_percent": round(self.disk_percent, 1),
            "load_avg_1m": round(self.load_avg_1m, 2),
            "load_avg_5m": round(self.load_avg_5m, 2),
            "load_avg_15m": round(self.load_avg_15m, 2),
            "process_rss_mb": round(self.process_rss_mb, 1),
            "timestamp": self.timestamp,
        }


class ResourceMonitor:
    """Lightweight system resource monitor using /proc and os stdlib."""

    # Thresholds for warnings
    RAM_WARNING_PCT = 85.0
    RAM_CRITICAL_PCT = 92.0
    CPU_WARNING_PCT = 90.0
    DISK_WARNING_PCT = 85.0

    def __init__(self):
        self._prev_cpu_times: Optional[tuple] = None
        self._prev_time: float = 0
        self._history: list[dict] = []
        self._max_history = 120  # keep ~30 min at 30s intervals

    def snapshot(self) -> ResourceSnapshot:
        """Take a resource snapshot using /proc and os."""
        cpu_pct = self._read_cpu_percent()
        ram = self._read_memory()
        disk = self._read_disk()
        load = os.getloadavg()
        proc_rss = self._read_process_rss()

        snap = ResourceSnapshot(
            cpu_percent=cpu_pct,
            ram_total_mb=ram[0],
            ram_used_mb=ram[1],
            ram_percent=ram[2],
            disk_total_gb=disk[0],
            disk_used_gb=disk[1],
            disk_percent=disk[2],
            load_avg_1m=load[0],
            load_avg_5m=load[1],
            load_avg_15m=load[2],
            process_rss_mb=proc_rss,
            timestamp=time.time(),
        )

        d = snap.to_dict()
        self._history.append(d)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        return snap

    def check_warnings(self, snap: ResourceSnapshot,
                       component_breakdown: Optional[dict] = None) -> list[dict]:
        """Generate smart warnings based on resource thresholds."""
        warnings = []

        component_note = self._format_component_breakdown(component_breakdown)

        if snap.ram_percent >= self.RAM_CRITICAL_PCT:
            warnings.append({
                "level": "critical",
                "component": "RAM",
                "message": f"Kritik RAM kullanımı: %{snap.ram_percent:.0f} "
                           f"({snap.ram_used_mb:.0f}/{snap.ram_total_mb:.0f} MB). "
                           f"Python process: {snap.process_rss_mb:.0f} MB. "
                           f"Ollama modeli RAM'in büyük bölümünü kullanıyor olabilir. "
                           f"Model GGUF quantize seviyesini düşürün (Q3_K_L)." + component_note,
                "value": snap.ram_percent,
            })
        elif snap.ram_percent >= self.RAM_WARNING_PCT:
            warnings.append({
                "level": "warning",
                "component": "RAM",
                "message": f"Yüksek RAM kullanımı: %{snap.ram_percent:.0f} "
                           f"({snap.ram_used_mb:.0f}/{snap.ram_total_mb:.0f} MB). "
                           f"Python ajanlar: {snap.process_rss_mb:.0f} MB kullanıyor." + component_note,
                "value": snap.ram_percent,
            })

        if snap.cpu_percent >= self.CPU_WARNING_PCT:
            warnings.append({
                "level": "warning",
                "component": "CPU",
                "message": f"Yüksek CPU kullanımı: %{snap.cpu_percent:.0f}. "
                           f"Load avg: {snap.load_avg_1m:.1f}/{snap.load_avg_5m:.1f}. "
                           f"LLM inference veya Strategist analizi yoğun olabilir." + component_note,
                "value": snap.cpu_percent,
            })

        if snap.disk_percent >= self.DISK_WARNING_PCT:
            warnings.append({
                "level": "warning",
                "component": "Disk",
                "message": f"Disk kullanımı yüksek: %{snap.disk_percent:.0f} "
                           f"({snap.disk_used_gb:.1f}/{snap.disk_total_gb:.1f} GB). "
                           f"Eski trade/log verileri temizlenebilir.",
                "value": snap.disk_percent,
            })

        return warnings

    def _format_component_breakdown(self, component_breakdown: Optional[dict]) -> str:
        """Render compact component breakdown for warning messages."""
        if not component_breakdown:
            return ""
        try:
            scored = []
            for name, data in component_breakdown.items():
                if not isinstance(data, dict):
                    continue
                score = float(data.get('activity_score', 0))
                if score <= 0:
                    continue
                scored.append((name, score))
            if not scored:
                return ""
            scored.sort(key=lambda x: x[1], reverse=True)
            top = ", ".join(f"{n}:{s:.1f}" for n, s in scored[:3])
            return f" | Bileşen yük skoru: {top}"
        except Exception:
            return ""

    def get_history(self) -> list[dict]:
        return self._history[-30:]  # last ~15 min

    def _read_cpu_percent(self) -> float:
        """Read CPU usage from /proc/stat."""
        try:
            with open("/proc/stat", "r") as f:
                line = f.readline()
            parts = line.split()
            # user, nice, system, idle, iowait, irq, softirq, steal
            times = tuple(int(p) for p in parts[1:9])
            now = time.monotonic()

            if self._prev_cpu_times is None:
                self._prev_cpu_times = times
                self._prev_time = now
                return 0.0

            deltas = tuple(a - b for a, b in zip(times, self._prev_cpu_times))
            total = sum(deltas)
            idle = deltas[3] + deltas[4]  # idle + iowait

            self._prev_cpu_times = times
            self._prev_time = now

            if total == 0:
                return 0.0
            return ((total - idle) / total) * 100.0
        except Exception:
            return 0.0

    def _read_memory(self) -> tuple[float, float, float]:
        """Read memory info from /proc/meminfo. Returns (total_mb, used_mb, percent)."""
        try:
            info = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    key, _, val = line.partition(":")
                    val = val.strip().split()[0]  # kB value
                    info[key.strip()] = int(val)

            total_kb = info.get("MemTotal", 0)
            available_kb = info.get("MemAvailable", info.get("MemFree", 0))
            total_mb = total_kb / 1024
            used_mb = (total_kb - available_kb) / 1024
            pct = (used_mb / total_mb * 100) if total_mb > 0 else 0
            return total_mb, used_mb, pct
        except Exception:
            return 0, 0, 0

    def _read_disk(self) -> tuple[float, float, float]:
        """Read disk usage for root filesystem."""
        try:
            st = os.statvfs("/")
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
            total_gb = total / (1024 ** 3)
            used_gb = used / (1024 ** 3)
            pct = (used_gb / total_gb * 100) if total_gb > 0 else 0
            return total_gb, used_gb, pct
        except Exception:
            return 0, 0, 0

    def _read_process_rss(self) -> float:
        """Read current process RSS from /proc/self/status."""
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024  # kB → MB
        except Exception:
            pass
        return 0.0
