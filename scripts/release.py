#!/usr/bin/env python3
"""Compatibility entry point for the split AI-Coach release workflow."""
from __future__ import annotations

import argparse
import sys

import release_check
from release_git import REPOSITORY


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    mode = value.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Run the local check and bundle stage")
    mode.add_argument("--release", action="store_true", help="Show the replacement deployment command")
    value.add_argument("--physiology-scheduler-migration", action="store_true")
    return value


def main(argv=None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if args.release:
        cli.error("Combined release is retired. Run release_check.py, then release_deploy.py --release --receipt RECEIPT "
                  f"--public-repo {REPOSITORY} --message TITLE")
    forwarded = ["--physiology-scheduler-migration"] if args.physiology_scheduler_migration else []
    print("scripts/release.py --check is deprecated; forwarding to scripts/release_check.py", file=sys.stderr)
    return release_check.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
