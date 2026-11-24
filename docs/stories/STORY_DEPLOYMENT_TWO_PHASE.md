# Story: Two-Phase Deployment Pipeline Integration & Validation

- **Status / Date**: Ready for Codex Implementation & Integration (2026-09-28)
- **Owner**: Gemini (Architecture / Specification) & Codex (Implementation / Execution)
- **Workspace**: `/Users/magnelima/Workspace/AI-Coach`
- **Branch**: `release/lightweight-dark-dashboard`

---

## 1. Problem & User Scenario
The legacy deployment workflow combined local test execution, asset building, Git publishing, CI polling, and Cloud Run deployment into a single monolithic script (`scripts/release.py`). This led to long cycle times, inability to inspect or validate release artifacts before deployment authorization, and potential timeout issues during live deployments.

**Goal**: Formalize and integrate the clean two-phase release model:
1. **Phase 1 (`release_check.py`)**: Local test suites, asset builds, Gitleaks scans, and generation of a sealed schema-v2 release receipt (`summary.json`) with an integrity digest.
2. **Phase 2 (`release_deploy.py`)**: Offline receipt validation (`--validate-only`) or authorized publication/deployment (`--release`) that strictly consumes the pre-checked artifact bundle without re-running local test suites.

---

## 2. Scope & Explicit Non-Goals

### In Scope
- Verification of `scripts/release_check.py`, `scripts/release_deploy.py`, and `scripts/release_artifacts.py`.
- Validation of schema-v2 receipt integrity (SHA256 digests, file modes, bundle hashes).
- Updating and verifying `tests/test_release_split.py` and existing release test suites.
- Providing `--validate-only` preflight mode for safe verification before user authorization.
- Backward compatibility wrapper in `scripts/release.py`.
- Ensuring documentation in `docs/DEPLOYMENT.md` and `docs/LOCAL_DEPLOY_PROMPT.md` matches the actual scripts.

### Non-Goals
- Executing a live Cloud Run deployment (requires explicit Magne release approval).
- Modifying IAM permissions or Cloud Run environment variables.
- Modifying physiology models or workout export schemas.

---

## 3. Data Contracts & Architecture

### Release Receipt v2 (`.local/release-checks/<run_id>/summary.json`)
```json
{
  "schema_version": 2,
  "run_id": "<timestamp-hex>",
  "created_at": "<ISO-8601>",
  "status": "checked",
  "local_checks_passed": true,
  "tree_sha": "<git-tree-sha>",
  "commit_sha": "<optional-commit-sha>",
  "public_repo": "limux92/ai-coach",
  "artifacts": {
    "files": {
      "path/to/file": {
        "sha256": "<hex>",
        "size": 1234,
        "mode": "100644"
      }
    },
    "bundles": {
      "backend": { "sha256": "<hex>", "size": 5678 },
      "gateway": { "sha256": "<hex>", "size": 91011 }
    }
  },
  "integrity_digest": "<sha256-of-canonical-representation>"
}
```

### Safety & Invariants
- `release_check.py` must abort if unstaged changes or untracked unignored files exist.
- `release_deploy.py` must verify the exact staged index tree and receipt integrity digest before initiating any remote actions.
- Tampered files, altered modes, or missing files in the receipt must immediately trigger an integrity failure.

---

## 4. Acceptance Criteria

1. **Check Phase Execution**:
   - Running `.venv/bin/python scripts/release_check.py` runs root pytest, adapter pytest, dashboard checks (`npm run check`), Gitleaks, creates the bundles, and writes a valid schema-v2 receipt under `.local/release-checks/`.
2. **Offline Validation Mode**:
   - Running `.venv/bin/python scripts/release_deploy.py --validate-only --receipt <path>` successfully verifies the receipt, bundles, and working tree without network calls.
3. **Tamper Resistance**:
   - Any manual modification to a file recorded in the receipt or to the receipt itself causes `--validate-only` to fail with a clear integrity error.
4. **Test Suite Coverage**:
   - `tests/test_release_split.py`, `tests/test_release.py`, and `tests/test_release_cloud.py` all pass (100% of release unit/mock tests passing).
5. **Workflow Attribution**:
   - Workload recorded via `scripts/workload.py` tracking Codex dev/test stages and GPT-oss assistance where used.

---

## 5. Codex Implementation Steps

1. **Step 1 (Test Suite & Verification)**: Run full test coverage across `tests/test_release_split.py`, `tests/test_release.py`, and `tests/test_release_cloud.py`.
2. **Step 2 (Local GPT-oss Dispatch)**: If additional receipt schema validation tests or helper functions are needed, Codex dispatches the bounded test/schema helper to `scripts/local_worker.py` (e.g. testing corrupt checksum edge cases in `test_release_split.py`).
3. **Step 3 (Integration & Smoke Check)**:
   - Perform a dry run of `scripts/release_check.py` (or targeted validation) in a clean/staged test environment.
   - Run `release_deploy.py --validate-only` on the generated receipt.
4. **Step 4 (Evidence Report)**: Record test results, output receipts, and diff status in Section 6 below.

---

## 6. Implementation Outcome (To be filled by Codex)
- **Changed Files**:
- **Check Evidence**:
- **GPT-oss Tasks Dispatched & Verified**:
- **Deviations / Findings**:

---

## 7. Lead Architect Review (To be completed by Gemini)
- **Review Status**: Pending Codex completion
- **Findings & Evaluation**:
