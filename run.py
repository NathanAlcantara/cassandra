#!/usr/bin/env python3
"""
Entry point for Cassandra.

Usage:
    python3 run.py <command> [options]
    python3 -m cassandra <command> [options]

See the options with: python3 run.py --help
"""

from cassandra.cli import run

if __name__ == "__main__":
    run()
