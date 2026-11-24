"""
Shared test fixtures and helpers.
"""

import pytest

from cassandra.config import Thresholds


@pytest.fixture
def thresholds():
    """Default thresholds matching the shipped config."""
    return Thresholds(
        cpu_usage_pct=90.0,
        cpu_psi_some=25.0,
        mem_available_pct_min=6.0,
        swap_free_pct_min=10.0,
        mem_psi_full=5.0,
        disk_psi_full=20.0,
        disk_space_used_pct=90.0,
    )


def make_sample(**overrides):
    """Build a resource sample with healthy defaults, overridable per test."""
    base = dict(
        cpu_usage_pct=10.0,
        psi_cpu_some_avg10=0.0,
        load1=1.0,
        load5=1.0,
        load15=1.0,
        mem_available_pct=50.0,
        mem_available_gb=7.5,
        mem_total_gb=15.0,
        swap_free_pct=80.0,
        swap_total=4 * 1024**3,
        swap_used_gb=0.8,
        psi_mem_full_avg10=0.0,
        psi_io_full_avg10=0.0,
        disk_worst_used_pct=55.0,
        disk_mount="/",
        disk_read_mbs=0.0,
        disk_write_mbs=0.0,
        net_rx_mbs=0.0,
        net_tx_mbs=0.0,
    )
    base.update(overrides)
    return base
