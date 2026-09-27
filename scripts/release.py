#!/usr/bin/env python3
"""Check AI-Coach, sync reviewed source to GitHub, and release both Cloud Run services."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import time
from uuid import uuid4
import zipfile

from release_git import GitRelease, REPOSITORY, ReleaseError, require

ROOT = Path(__file__).resolve().parents[1]


class Runner:
    """Visible bounded stages; detailed output remains in ignored private logs."""
    def __init__(self, directory, receipt):
        self.directory, self.receipt = directory, receipt
        self.counter = 0

    def save(self):
        (self.directory / "summary.json").write_text(json.dumps(self.receipt, indent=2) + "\n")

    def __call__(self, args, *, label, cwd=None, timeout=900, allowed=(0,)):
        self.counter += 1
        log = self.directory / f"{self.counter:03d}.log"
        self.receipt["stage"] = label
        self.receipt["last_log"] = str(log)
        self.save()
        print(f"[{self.counter}] {label} | {log.name}", flush=True)
        started = time.monotonic()
        try:
            process = subprocess.Popen(args, cwd=cwd or ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
        except OSError:
            log.write_text("Could not start executable: " + str(args[0]) + "\n")
            raise ReleaseError(f"Cannot start {label}; check executable {args[0]}.") from None
        try:
            while True:
                try:
                    output, error = process.communicate(timeout=min(15, timeout))
                    break
                except subprocess.TimeoutExpired:
                    require(time.monotonic() - started < timeout, label + " timed out; inspect the log.")
                    print(f"  {label}: still running ({int(time.monotonic() - started)}s)", flush=True)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                output, error = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                output, error = process.communicate()
            log.write_text(output + "\n" + error)
            raise
        log.write_text(output + "\n" + error)
        require(process.returncode in allowed, f"{label} failed (exit {process.returncode}). Inspect {log}; no automatic retry.")
        return output


def checks(run, git):
    require(not any(name.startswith("VITE_") for name in os.environ), "Unset VITE_* environment overrides before building.")
    env_files = [p for p in (ROOT / "dashboard").glob(".env*") if p.name != ".env.example"]
    require(not env_files, "Remove private dashboard .env overrides from this build; release assets must be reproducible.")
    for path in (ROOT / ".venv/bin/python", ROOT / "adapters/mcp/.venv/bin/python", ROOT / ".tools/gitleaks/gitleaks"):
        require(path.is_file(), "Missing prerequisite: " + str(path))
    run([str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q"], label="Backend tests")
    run([str(ROOT / "adapters/mcp/.venv/bin/python"), "-m", "pytest", "-q"],
        cwd=ROOT / "adapters/mcp", label="MCP adapter tests")
    run(["npm", "--prefix", "dashboard", "run", "check"], label="Dashboard formatting, tests, build and budgets")
    scanner = str(ROOT / ".tools/gitleaks/gitleaks")
    run([scanner, "git", "--staged", "--pre-commit", "--redact", "--no-banner", "."], label="Staged secret scan")
    run([scanner, "git", "--redact", "--no-banner", "--log-opts=--all", "."], label="Full-history secret scan")
    assets = ROOT / "adapters/mcp/static/dashboard"
    run([scanner, "dir", "--redact", "--no-banner", str(assets)], label="Built assets secret scan")
    require((assets / "index.html").is_file(), "Dashboard build did not produce index.html.")
    manifest = {}
    for path in sorted(assets.rglob("*")):
        require(not path.is_symlink(), "Symlinks are not allowed in the dashboard bundle.")
        if path.is_file():
            manifest[path.relative_to(assets).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (run.directory / "assets.json").write_text(json.dumps(manifest, indent=2) + "\n")
    run.receipt["local_checks_passed"] = True
    return manifest


def bundle(run, git, sha, assets):
    """Use committed source for both services plus the exact checked dashboard build."""
    archive = run.directory / "source.zip"
    git.git("archive", "--format=zip", "--output=" + str(archive), sha)
    destination = run.directory / "source"
    destination.mkdir()
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            name = PurePosixPath(item.filename)
            require(not name.is_absolute() and ".." not in name.parts, "Unsafe source archive entry.")
            require((item.external_attr >> 16) & 0o170000 != 0o120000, "Symlinks are not allowed in the release bundle.")
            target = destination / item.filename
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.read(item))
    source = destination / "adapters/mcp"
    for name, digest in assets.items():
        content = (ROOT / "adapters/mcp/static/dashboard" / name).read_bytes()
        require(hashlib.sha256(content).hexdigest() == digest, "Build assets changed during GitHub CI; rerun release.")
        target = source / "static/dashboard" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return {"ai-coach-sync": destination, "ai-coach-chat": source}


def release(run, git, args, run_id):
    git.inspect(strict=True)
    if getattr(args, "physiology_scheduler_migration", False):
        git.prepare(physiology_migration=True)
    else:
        git.prepare()
    tree = git.fingerprint()
    # Frozen installation for the frontend. Python venvs must already match their lockfiles.
    run(["npm", "--prefix", "dashboard", "ci", "--no-audit", "--no-fund"], label="Install locked dashboard dependencies")
    assets = checks(run, git)
    git.unchanged(tree)
    from release_cloud import CloudRelease
    cloud = CloudRelease(run, run.receipt)
    scheduler = None
    if getattr(args, "physiology_scheduler_migration", False):
        from release_scheduler import SchedulerMigration
        scheduler = SchedulerMigration(cloud)
        scheduler.preflight()
    if args.bootstrap_backend_health:
        cloud.preflight(bootstrap_backend_health=args.bootstrap_backend_health)
    else:
        cloud.preflight()
    pr, sha = git.publish(tree, args.message, run_id)
    run.save()
    git.wait_for_ci(pr, sha)
    git.unchanged(tree)
    sources = bundle(run, git, sha, assets)
    # Each upload uses its own committed .gcloudignore; no working-tree private files enter the bundle.
    for name, source in sources.items():
        manifest = cloud.command("meta", "list-files-for-upload", cwd=source)
        (run.directory / ("upload-" + name + ".txt")).write_text(manifest)
    run([str(ROOT / ".tools/gitleaks/gitleaks"), "dir", "--redact", "--no-banner", str(run.directory / "source")],
        label="Cloud source secret scan")
    def before_promote():
        git.assert_passed(pr, sha)
        git.unchanged(tree)
    cloud.deploy(sources, assets, sha, run_id, before_promote)
    if scheduler:
        scheduler.apply()
    run.receipt["status"] = "released"


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    mode = value.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Show local file scope only (default)")
    mode.add_argument("--check", action="store_true", help="Local checks only; no commits, pushes or cloud access")
    mode.add_argument("--release", action="store_true", help="Commit staged files, push/PR, wait CI, deploy backend and gateway")
    value.add_argument("--public-repo", choices=[REPOSITORY], help="Acknowledge the reviewed public publication destination")
    value.add_argument("--message", help="Reviewed commit and PR title (required for --release)")
    value.add_argument("--bootstrap-backend-health", metavar="EXISTING_REVISION",
                       help="Explicit health-route migration from this exact serving backend revision; candidates stay strict")
    value.add_argument("--physiology-scheduler-migration", action="store_true",
                       help="Publish the exact reviewed Scheduler diff and update only its deadline/retry policy after service release")
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    if args.bootstrap_backend_health and not args.release:
        parser().error("--bootstrap-backend-health requires --release")
    if args.physiology_scheduler_migration and not args.release:
        parser().error("--physiology-scheduler-migration requires --release")
    if args.release and (args.public_repo != REPOSITORY or not args.message or not args.message.strip()):
        parser().error("--release requires --public-repo limux92/ai-coach and --message TITLE")
    os.umask(0o077)
    parent = ROOT / ".local/releases"
    parent.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%y%m%d-%H%M%S") + "-" + uuid4().hex[:4]
    directory = parent / run_id
    directory.mkdir()
    receipt = {"status": "running", "mode": "release" if args.release else "check" if args.check else "plan",
               "repository": REPOSITORY, "services": ["ai-coach-sync", "ai-coach-chat"], "started_at": run_id}
    run = Runner(directory, receipt)
    print("Release receipts: " + str(directory), flush=True)
    try:
        with (parent / "release.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ReleaseError("Another release/check is running in this workspace.") from None
            git = GitRelease(run, ROOT, receipt)
            if args.release:
                release(run, git, args, run_id)
            else:
                git.inspect()
                if args.check:
                    checks(run, git)
                receipt["status"] = "checked" if args.check else "planned"
        print(f"Completed: {receipt['status']}. Receipt: {directory / 'summary.json'}", flush=True)
        if args.release:
            print(receipt["pull_request"] + " (open; not automatically merged)", flush=True)
            print(receipt["public_url"], flush=True)
            for name, revision in receipt["revisions"].items():
                print(name + " | " + revision, flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        receipt["status"] = "failed"
        # Avoid dumping unexpected provider responses, environment values or tokens.
        message = str(error) if isinstance(error, ReleaseError) else type(error).__name__ + ": inspect the last stage log."
        receipt["error"] = message
        print("STOPPED: " + message, file=sys.stderr, flush=True)
        print("Receipt: " + str(directory / "summary.json"), file=sys.stderr, flush=True)
        return 1
    finally:
        run.save()


if __name__ == "__main__":
    sys.exit(main())
