#!/usr/bin/env python3
"""Deploy an exact checked receipt through GitHub CI and both Cloud Run services."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4

from release_artifacts import file_sha256, load_receipt, verify_artifacts
from release_git import GitRelease, REPOSITORY, ReleaseError, require

ROOT = Path(__file__).resolve().parents[1]


class Runner:
    """Run Git and Cloud commands with private stage logs and bounded timeouts."""

    def __init__(self, directory: Path, receipt: dict):
        self.directory, self.receipt = directory, receipt
        self.counter = 0

    def save(self):
        (self.directory / "summary.json").write_text(json.dumps(self.receipt, indent=2) + "\n")

    def __call__(self, args, *, label, cwd=None, timeout=900, allowed=(0,)):
        self.counter += 1
        log = self.directory / f"{self.counter:03d}.log"
        self.receipt.update(stage=label, last_log=str(log))
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
        require(process.returncode in allowed,
                f"{label} failed (exit {process.returncode}). Inspect {log}; no automatic retry.")
        return output


def git_cmd(*args: str, cwd: Path = ROOT) -> str:
    """Run a local Git command for read-only validation."""
    process = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    require(process.returncode == 0,
            f"Git command failed (git {' '.join(args)}): {process.stderr.strip()}")
    return process.stdout.strip()


def verify_tree_invariant(actual_tree: str, expected_tree: str, context: str = "Tree") -> None:
    require(actual_tree == expected_tree, f"{context} mismatch: expected {expected_tree} got {actual_tree}")


def verify_commit_tree(git_fn, commit_ref: str, expected_tree: str) -> None:
    commit_tree = git_fn("rev-parse", f"{commit_ref}^{{tree}}").strip()
    verify_tree_invariant(commit_tree, expected_tree, context=f"Commit {commit_ref}^{{tree}}")


def verify_index_tree(git_fn, expected_tree: str) -> None:
    current_tree = git_fn("write-tree").strip()
    verify_tree_invariant(current_tree, expected_tree, context="Current index tree")


def verify_clean_workspace(git_fn) -> None:
    """Reject source outside the checked index tree."""
    conflicts = git_fn("diff", "--name-only", "--diff-filter=U", "-z")
    unstaged = git_fn("diff", "--name-only", "-z")
    untracked = git_fn("ls-files", "--others", "--exclude-standard", "-z")
    require(not conflicts, "Resolve Git conflicts before deployment validation.")
    require(not unstaged and not untracked,
            "Workspace has unstaged or untracked files; deploy only the exact checked source.")


def verify_deployment_readiness(
    receipt_path: Path,
    git_fn=None,
    commit_ref: str | None = None,
) -> tuple[dict, Path, dict[str, Path]]:
    """Validate the receipt, artifacts, clean workspace, index, and optional commit."""
    path = Path(receipt_path)
    receipt = load_receipt(path)
    receipt_dir = (path if path.is_dir() else path.parent).resolve()
    verify_artifacts(receipt_dir, receipt)
    if git_fn is not None:
        verify_clean_workspace(git_fn)
        verify_index_tree(git_fn, receipt["tree"])
        if commit_ref is not None:
            verify_commit_tree(git_fn, commit_ref, receipt["tree"])
    bundle_paths = {
        service: receipt_dir / descriptor["path"]
        for service, descriptor in receipt["bundles"].items()
    }
    return receipt, receipt_dir, bundle_paths


def print_deployment_plan(receipt: dict, receipt_dir: Path, bundle_paths: dict[str, Path],
                          commit_ref: str | None = None) -> None:
    print("=== Deployment Validation Plan ===", flush=True)
    print(f"Receipt:        {receipt_dir / 'summary.json'}", flush=True)
    print(f"Run ID:         {receipt.get('started_at', 'unknown')}", flush=True)
    print(f"Repository:     {receipt['repository']}", flush=True)
    print(f"Tree SHA:       {receipt['tree']}", flush=True)
    if commit_ref:
        print(f"Commit Ref:     {commit_ref} (tree verified)", flush=True)
    print("Services:", flush=True)
    for service, path in bundle_paths.items():
        print(f"  - {service}: {path}", flush=True)
    print(f"Artifacts:      {len(receipt['bundle_manifest'])} bundle files, "
          f"{len(receipt['assets'])} dashboard assets", flush=True)
    print(f"Integrity:      {receipt['integrity_digest']} (verified)", flush=True)
    print("Outcome:        VALIDATED (no external mutations performed)", flush=True)


def verify_release_options(check_receipt: dict, args) -> None:
    has_scheduler_review = "reviewed_infra_diff_sha256" in check_receipt
    require(has_scheduler_review == args.physiology_scheduler_migration,
            "Scheduler migration flag must exactly match the checked receipt.")


def deploy(run: Runner, check_receipt: dict, check_dir: Path,
           bundles: dict[str, Path], args, run_id: str) -> None:
    """Publish the checked tree, require exact CI, then deploy its sealed bundles."""
    verify_release_options(check_receipt, args)
    git = GitRelease(run, ROOT, run.receipt)
    git.inspect(strict=True)
    git.prepare(physiology_migration=args.physiology_scheduler_migration)
    require(run.receipt.get("publication_files") == check_receipt["publication_files"],
            "Publication scope changed since checks; rerun release_check.py.")
    if args.physiology_scheduler_migration:
        require(run.receipt.get("reviewed_infra_diff_sha256")
                == check_receipt.get("reviewed_infra_diff_sha256"),
                "Reviewed infrastructure diff changed since checks.")
    tree = check_receipt["tree"]
    require(git.fingerprint() == tree, "Current index tree differs from checked receipt.")

    from release_cloud import CloudRelease
    cloud = CloudRelease(run, run.receipt)
    scheduler = None
    if args.physiology_scheduler_migration:
        from release_scheduler import SchedulerMigration
        scheduler = SchedulerMigration(cloud)
        scheduler.preflight()
    cloud.preflight(
        bootstrap_backend_health=args.bootstrap_backend_health,
        recover_failed_backend_candidate=args.recover_failed_backend_candidate,
    )

    pr, commit = git.publish(tree, args.message, run_id)
    verify_commit_tree(git.git, commit, tree)
    run.receipt["phase"] = "published"
    run.save()
    git.wait_for_ci(pr, commit)
    verify_commit_tree(git.git, commit, tree)
    verify_artifacts(check_dir, check_receipt)
    run.receipt["phase"] = "ci-passed"
    run.save()

    for service, source in bundles.items():
        manifest = cloud.command("meta", "list-files-for-upload", cwd=source)
        (run.directory / ("upload-" + service + ".txt")).write_text(manifest)

    def before_promote():
        git.assert_passed(pr, commit)
        verify_commit_tree(git.git, commit, tree)
        verify_artifacts(check_dir, check_receipt)

    cloud.deploy(bundles, check_receipt["assets"], commit, run_id, before_promote)
    if scheduler:
        scheduler.apply()
    run.receipt.update(status="released", phase="verified", check_tree=tree,
                       check_integrity_digest=check_receipt["integrity_digest"])


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    mode = value.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true",
                      help="Verify the checked receipt and local tree without external access")
    mode.add_argument("--release", action="store_true",
                      help="Publish through GitHub CI and deploy both Cloud Run services")
    value.add_argument("--receipt", type=Path, required=True,
                       help="Path to a successful release_check.py receipt")
    value.add_argument("--commit", help="Optional commit ref checked by --validate-only")
    value.add_argument("--public-repo", choices=[REPOSITORY],
                       help="Acknowledge the reviewed public destination")
    value.add_argument("--message", help="Reviewed commit and PR title")
    value.add_argument("--bootstrap-backend-health", metavar="EXISTING_REVISION")
    value.add_argument("--physiology-scheduler-migration", action="store_true")
    value.add_argument("--recover-failed-backend-candidate", metavar="FAILED_REVISION")
    return value


def validate_cli(args, cli) -> None:
    release_only = (args.public_repo, args.message, args.bootstrap_backend_health,
                    args.physiology_scheduler_migration, args.recover_failed_backend_candidate)
    if args.validate_only and any(release_only):
        cli.error("Release options require --release")
    if args.release and (args.public_repo != REPOSITORY or not args.message or not args.message.strip()):
        cli.error(f"--release requires --public-repo {REPOSITORY} and --message TITLE")
    if args.release and args.commit:
        cli.error("--commit is only for --validate-only")


def main(argv=None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    validate_cli(args, cli)
    try:
        check_receipt, check_dir, bundles = verify_deployment_readiness(
            args.receipt, git_fn=git_cmd, commit_ref=args.commit)
        if args.validate_only:
            print_deployment_plan(check_receipt, check_dir, bundles, commit_ref=args.commit)
            return 0

        os.umask(0o077)
        parent = ROOT / ".local/deployments"
        parent.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("%y%m%d-%H%M%S") + "-" + uuid4().hex[:4]
        directory = parent / run_id
        directory.mkdir()
        receipt = {
            "schema_version": 1,
            "status": "running",
            "phase": "validated",
            "mode": "deploy",
            "repository": REPOSITORY,
            "services": list(check_receipt["services"]),
            "started_at": run_id,
            "check_receipt": str(check_dir / "summary.json"),
            "check_receipt_sha256": file_sha256(check_dir / "summary.json"),
            "check_tree": check_receipt["tree"],
            "check_integrity_digest": check_receipt["integrity_digest"],
        }
        run = Runner(directory, receipt)
        run.save()
        print("Deployment receipt: " + str(directory / "summary.json"), flush=True)
        try:
            lock_path = ROOT / ".local/release.lock"
            with lock_path.open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ReleaseError("Another release check or deployment is running.") from None
                deploy(run, check_receipt, check_dir, bundles, args, run_id)
            print("Completed: released. Receipt: " + str(directory / "summary.json"), flush=True)
            print(receipt["pull_request"] + " (open; not automatically merged)", flush=True)
            print(receipt["public_url"], flush=True)
            return 0
        except (Exception, KeyboardInterrupt) as error:
            receipt["status"] = "failed"
            message = str(error) if isinstance(error, ReleaseError) else type(error).__name__ + ": inspect last stage log."
            receipt["error"] = message
            print("STOPPED: " + message, file=sys.stderr, flush=True)
            print("Receipt: " + str(directory / "summary.json"), file=sys.stderr, flush=True)
            return 1
        finally:
            run.save()
    except (Exception, KeyboardInterrupt) as error:
        message = str(error) if isinstance(error, ReleaseError) else f"{type(error).__name__}: {error}"
        print("DEPLOYMENT VALIDATION FAILED: " + message, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
