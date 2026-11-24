"""
Command-line interface: argument parsing, logging setup, and dispatch to the
daemon (`run`) or the operational commands (report, incidents, clear).

Service lifecycle commands (start/stop/status) live in the `cassie` shell
wrapper, since they drive systemd. This module owns everything that is Python.
"""

import argparse
import logging
import os
import sys
from datetime import datetime

from . import __version__, config, monitor, report, storage
from .constants import log

# Project root (one level above this package) — where config.ini lives.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def configure_logging(verbose=False):
    """Configure logging to stdout with a timestamp."""
    logging.basicConfig(
        level=(logging.DEBUG if verbose else logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def build_parser():
    parser = argparse.ArgumentParser(
        prog="cassandra",
        description="Cassandra — a lightweight resource-bottleneck monitor.",
    )
    parser.add_argument("--version", action="version",
                        version=f"cassandra {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="verbose (debug) logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="run the monitoring loop (used by the service)")

    p_report = sub.add_parser("report", help="generate the monthly report")
    p_report.add_argument("month", nargs="?",
                          help="month YYYY-MM (default: current month)")

    sub.add_parser("incidents", help="list recorded incidents")
    sub.add_parser("clear", help="wipe all collected data (samples + incidents)")

    return parser


def cmd_run(cfg):
    monitor.run(cfg)
    return 0


def cmd_report(cfg, month):
    month = month or datetime.now().strftime("%Y-%m")
    try:
        path = report.generate(cfg.db_path, cfg.reports_dir, month)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    print(f"Report generated: {path}")
    return 0


def cmd_incidents(cfg):
    import sqlite3
    if not os.path.exists(cfg.db_path):
        print("No data yet. Is Cassandra running?")
        return 0
    conn = sqlite3.connect(cfg.db_path)
    rows = conn.execute(
        "SELECT id,start_ts,duration_sec,category FROM incidents "
        "ORDER BY start_ts").fetchall()
    if not rows:
        print("No incidents recorded (the machine has been healthy).")
    else:
        print(f"{len(rows)} incident(s):")
        for r in rows:
            print(f"  #{r[0]} | {r[1]} | {r[2]}s | {r[3]}")
    conn.close()
    return 0


def cmd_clear(cfg):
    if not os.path.exists(cfg.db_path):
        print("Nothing to clear.")
        return 0
    storage.clear_all(cfg.db_path)
    print("All collected data was wiped (samples + incidents).")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    cfg = config.build_config(PROJECT_ROOT)

    if args.command == "run":
        return cmd_run(cfg)
    if args.command == "report":
        return cmd_report(cfg, args.month)
    if args.command == "incidents":
        return cmd_incidents(cfg)
    if args.command == "clear":
        return cmd_clear(cfg)
    return 1


def run():
    """Entry point wrapper (used by run.py and __main__)."""
    sys.exit(main())
