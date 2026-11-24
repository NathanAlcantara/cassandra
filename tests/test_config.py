import textwrap

from cassandra import config


def test_defaults_when_no_config(tmp_path):
    # empty dir -> everything falls back to constants defaults
    cfg = config.build_config(str(tmp_path))
    assert cfg.sample_interval == 5
    assert cfg.thresholds.mem_available_pct_min == 6.0
    assert cfg.pwa_watch.enabled is False
    assert cfg.network.public_ref == "8.8.8.8"
    assert cfg.network.http_ttfb_warn == 3.0
    assert cfg.network.sustained_probes == 2
    assert cfg.open_command == "xdg-open"
    assert cfg.notification_timeout_ms == 20000


def test_reads_overrides(tmp_path):
    (tmp_path / "config.ini").write_text(textwrap.dedent("""
        [general]
        sample_interval_sec = 10
        [memory]
        available_pct_min = 15
        [network]
        targets = gh|https://github.com , mv|https://maven.example.com
        [pwa_watch]
        enabled = true
        threshold_mb = 2000
    """))
    cfg = config.build_config(str(tmp_path))
    assert cfg.sample_interval == 10
    assert cfg.thresholds.mem_available_pct_min == 15.0
    assert cfg.pwa_watch.enabled is True
    assert cfg.pwa_watch.threshold_bytes == 2000 * 1024 * 1024


def test_parse_targets():
    targets = config._parse_targets(
        "github|https://github.com , maven|https://maven.example.com")
    assert targets == [
        ("github", "https://github.com"),
        ("maven", "https://maven.example.com"),
    ]


def test_parse_targets_ignores_malformed():
    assert config._parse_targets("bad, , also-bad") == []
