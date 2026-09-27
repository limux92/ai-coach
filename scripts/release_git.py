"""Git publication gates for the routine release command (no automatic staging)."""
from __future__ import annotations

import json
import hashlib
from pathlib import PurePosixPath
import time

REPOSITORY = "limux92/ai-coach"
REMOTE_URLS = {f"https://github.com/{REPOSITORY}.git", f"git@github.com:{REPOSITORY}.git"}
CHECK_NAMES = {"Backend tests", "MCP adapter tests", "Dashboard tests and build", "Scan Git history for secrets"}
PRIVATE_PARTS = {".local", ".tools", ".venv", "node_modules", "data", "exports", "credentials"}
PRIVATE_SUFFIXES = {".fit", ".tcx", ".gpx", ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".log"}
# Separately reviewed Scheduler-only diff against origin/main, 27 September 2026.
PHYSIOLOGY_INFRA_DIFF = "4244840140567c8e80928efb46c19dce96fd3376be169475cc4f271657886d50"


class ReleaseError(RuntimeError):
    """Actionable operator error without provider response bodies."""


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def validate_paths(paths):
    """Supplement secret scanning with known private-file exclusions."""
    for name in paths:
        path = PurePosixPath(name)
        parts = {part.lower() for part in path.parts}
        private_env = path.name.startswith(".env") and path.name != ".env.example"
        require(not (parts & PRIVATE_PARTS or path.suffix.lower() in PRIVATE_SUFFIXES
                     or private_env or name.startswith("adapters/mcp/static/dashboard/")
                     or path.name in {"deployment.json", "STATUS.md"}),
                f"Private/generated path cannot be published: {name}")


def routine_scope(paths, *, physiology_migration=False, infra_diff_sha256=None):
    infrastructure = sorted(p for p in paths if p.startswith("infra/"))
    if physiology_migration:
        require(infrastructure == ["infra/deploy.py"] and infra_diff_sha256 == PHYSIOLOGY_INFRA_DIFF,
                "Scheduler migration must match the exact separately reviewed infrastructure diff.")
    else:
        require(not infrastructure,
                "Infrastructure changes need separate review; this command updates existing backend and gateway code.")


def ci_complete(checks):
    """Only the complete, successful named CI suite can unlock deployment."""
    relevant = [c for c in checks if c.get("workflow") == "CI"]
    if not CHECK_NAMES.issubset({c.get("name") for c in relevant}):
        return False
    for check in relevant:
        require(check.get("bucket") not in {"fail", "cancel", "skipping"},
                f"GitHub CI did not pass: {check.get('name')}")
    return all(c.get("bucket") == "pass" for c in relevant)


class GitRelease:
    def __init__(self, run, root, receipt):
        self.run, self.root, self.receipt = run, root, receipt

    def git(self, *args):
        return self.run(["git", *args], label="git " + args[0]).strip()

    def gh(self, *args, allowed=(0,)):
        return self.run(["gh", *args, "--repo", REPOSITORY], label="GitHub " + args[0], timeout=120, allowed=allowed).strip()

    def paths(self, *args):
        return [p for p in self.git(*args, "-z").split("\0") if p]

    def inspect(self, *, strict=False):
        require(self.git("rev-parse", "--show-toplevel") == str(self.root), "Run from the AI-Coach checkout.")
        for args in (("remote", "get-url", "--all", "origin"),
                     ("remote", "get-url", "--push", "--all", "origin")):
            require(self.git(*args) in REMOTE_URLS, "origin must point only to public limux92/ai-coach.")
        require(not self.paths("diff", "--name-only", "--diff-filter=U"), "Resolve Git conflicts first.")
        staged = self.paths("diff", "--cached", "--name-only")
        validate_paths(self.paths("ls-files"))
        require(all(line.split()[0] in {"100644", "100755"}
                    for line in self.git("ls-files", "--stage").splitlines()),
                "Symlinks/submodules need separate release review.")
        self.git("diff", "--check")
        self.git("diff", "--cached", "--check")
        unstaged = self.paths("diff", "--name-only")
        untracked = self.paths("ls-files", "--others", "--exclude-standard")
        if strict:
            require(not unstaged and not untracked,
                    "Review and stage intended changes first; unstaged/untracked files remain. No files were auto-staged.")
        self.receipt.update(staged_files=staged, unstaged_files=unstaged, untracked_files=untracked)
        (self.run.directory / "files.json").write_text(json.dumps(self.receipt, indent=2) + "\n")
        print(f"Staging area: {len(staged)} paths; unstaged: {len(unstaged)}; untracked: {len(untracked)}", flush=True)
        for path in staged:
            print("  " + path, flush=True)
        return staged

    def fingerprint(self):
        # Strict inspect has already rejected unstaged and untracked source.
        return self.git("write-tree")

    def unchanged(self, tree):
        require(not self.paths("diff", "--name-only")
                and not self.paths("ls-files", "--others", "--exclude-standard"),
                "Workspace changed during release. Review and rerun.")
        require(self.fingerprint() == tree, "Staged source changed during checks. Run the checks again.")

    def prepare(self, *, physiology_migration=False):
        self.git("fetch", "origin", "main")
        self.git("merge-base", "--is-ancestor", "origin/main", "HEAD")
        paths = self.paths("diff", "--name-only", "origin/main")
        digest = None
        if physiology_migration:
            patch = self.git("diff", "--binary", "--full-index", "origin/main", "--", "infra/") + "\n"
            digest = hashlib.sha256(patch.encode()).hexdigest()
        routine_scope(paths, physiology_migration=physiology_migration, infra_diff_sha256=digest)
        if physiology_migration:
            self.receipt["reviewed_infra_diff_sha256"] = digest
        self.receipt["publication_files"] = paths
        (self.run.directory / "publication-files.json").write_text(json.dumps(paths, indent=2) + "\n")
        print("Publication scope (including existing local commits):", flush=True)
        for path in paths:
            print("  " + path, flush=True)
        require(self.git("rev-parse", "--is-shallow-repository") == "false", "Use full Git history for the secret scan.")

    def publish(self, tree, message, run_id):
        self.unchanged(tree)
        branch = self.git("branch", "--show-current")
        require(bool(branch), "Detached HEAD: select a release branch first.")
        if branch in {"main", "master"}:
            branch = "release/" + run_id
            self.git("switch", "-c", branch)
        if self.paths("diff", "--cached", "--name-only"):
            self.git("commit", "-m", message)
        self.unchanged(tree)
        require(self.git("rev-parse", "HEAD^{tree}") == tree, "Commit hooks changed tested source; rerun checks.")
        sha = self.git("rev-parse", "HEAD")
        require(sha != self.git("rev-parse", "origin/main"), "No release changes relative to main.")
        self.git("push", "--set-upstream", "origin", f"HEAD:refs/heads/{branch}")
        require(self.git("ls-remote", "origin", f"refs/heads/{branch}").split()[0] == sha,
                "Remote branch does not match the tested commit.")
        prs = json.loads(self.gh("pr", "list", "--head", branch, "--base", "main", "--state", "open", "--json", "number,url"))
        if not prs:
            body = self.run.directory / "pr-body.md"
            body.write_text("Routine backend and gateway release. Local Python tests, frontend formatting/tests/build/budgets, "
                            "and staged/full-history secret scans passed.\n\n"
                            "Cloud deployment runs only after the exact PR head passes all four CI jobs. "
                            "Deployment results remain in private local receipts. This PR is not automatically merged.\n")
            self.gh("pr", "create", "--base", "main", "--head", branch,
                    "--title", message, "--body-file", str(body))
        pr = json.loads(self.gh("pr", "view", branch, "--json", "number,url,headRefOid,baseRefName,state,isCrossRepository"))
        require(pr["headRefOid"] == sha and pr["baseRefName"] == "main" and pr["state"] == "OPEN"
                and pr["isCrossRepository"] is False,
                "PR must contain the tested commit and target main.")
        self.receipt.update(commit=sha, branch=branch, pull_request=pr["url"])
        return str(pr["number"]), sha

    def wait_for_ci(self, pr, sha, timeout=1800):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.assert_head(pr, sha)
            checks = self.checks(pr) if state.get("statusCheckRollup") else []
            if ci_complete(checks):
                self.receipt["github_ci_passed"] = True
                return
            print("Waiting for all four GitHub CI jobs on " + sha[:12], flush=True)
            time.sleep(15)
        raise ReleaseError("GitHub CI timed out; no cloud deployment started. Rerun after resolving CI.")

    def assert_head(self, pr, sha):
        value = json.loads(self.gh("pr", "view", pr, "--json", "headRefOid,state,baseRefName,isCrossRepository,statusCheckRollup"))
        require(value["headRefOid"] == sha and value["state"] == "OPEN" and value["baseRefName"] == "main" and value["isCrossRepository"] is False,
                "PR changed or closed while releasing; no further deployment is allowed.")
        return value

    def checks(self, pr):
        return json.loads(self.gh("pr", "checks", pr, "--json", "name,state,bucket,workflow", allowed=(0, 1, 8)))

    def assert_passed(self, pr, sha):
        self.assert_head(pr, sha)
        require(ci_complete(self.checks(pr)), "GitHub CI is no longer fully passing; promotion stopped.")
