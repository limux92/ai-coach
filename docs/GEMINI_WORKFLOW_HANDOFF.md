# Gemini workflow handoff

Updated 28 September 2026. This is the authoritative workflow handoff requested
by Magne when moving the main conversation to Gemini. It supersedes older role
assignments that made Codex the conversational lead and Gemini an infrastructure
worker. It records decisions and evidence; it does not authorize a deployment.

## 2 October continuation

Outcomes completed & verified:
1. GoldenCheetah Roadmap A (PMC & Critical Power MMP curve):
   - Continuous PMC & CP/MMP dashboard views deployed to production.
   - Dynamic CP estimation from actual workout MMP profile when model is missing.
2. Roadmap B (Multi-User Platform & Commercialization):
   - Step 1: Multi-tenant database isolation (Firestore rules, Store.for_user, gateway X-User-Id forwarding).
   - Step 2: Athlete registration with Google Sign-In and pending_payment onboarding view.
   - Step 3: Payment wall & Terms of Sale with Vipps MobilePay recurring agreements v3 (199 NOK/mo) & return activation, plus Stripe fallback.
   - Step 4: Multi-tenant Intervals.icu credentials storage and background sync runner with rate budgeting.
   - Step 5: Scheduler integration & E2E verification test suite (`scripts/test_onboarding_billing.py` passing 8/8 checks).
3. Quality gates: 638 backend tests, 97 adapter tests, 20 dashboard tests pass; JS gzip 49.9 KB / CSS 5.6 KB within limits.

## 1. Decision and responsibilities

Magne's instruction: “I am switching to Gemini for dialog, architecture and research.”

| Responsibility | Owner |
| --- | --- |
| Main conversation, requirements and story selection | Gemini with Magne |
| Research, architecture, design alternatives and trade-offs | Gemini |
| Agreed implementation plan and acceptance criteria | Gemini, informed by source and Codex feasibility feedback |
| Application development, infrastructure code, scripts and automation | Codex |
| Debugging, tests, integration and execution evidence | Codex |
| Independent review of the result against the agreed design | Gemini |
| Corrections and final technical verification | Codex |
| Product choices and authorization of publication/access changes | Magne |

Use Gemini in its normal working environment with relevant repository discovery
and research tools. Do not force the main dialogue or architectural investigation
through the bounded worker harness. Codex is the default implementation executor;
both agents may challenge a design with evidence. Neither role means unquestioned
authority, and neither model is assumed universally better.

GPT-oss is optional for small, useful drafts. Do not involve a third model just to
claim participation or token savings. Its existing one-file/function and visible
VS Code terminal requirements still apply when it is used.

## 2. Start here in Gemini

1. Read this document, `../AGENTS.md` and `../README_CURRENT_CONTEXT.md`.
2. Inspect the primary workspace and, when relevant, the isolated checkout below.
   Preserve existing staged, unstaged and untracked work.
3. Treat production observations and test counts as dated evidence. Recheck the
   appropriate live state before claiming a feature is currently working.
4. Explain the current position briefly to Magne, agree the next story, and write
   a shared implementation contract. Do not automatically resume old Gemini jobs.

Magne prefers practical, medium-length explanations, clear completion evidence
and few interruptions. Do not repeatedly seek approval for already authorized
local work. Prepare a concrete reviewed payload before requesting release approval.
Never close, restart or reload VS Code without an explicit request.

## 3. Collaboration loop

Gemini investigates the problem and maintains one design document per story.
Give Codex the exact document path, working directory and current Git reference.
Provide access to relevant complete modules and tests, not only fragments chosen
before dependencies are understood. Include useful history as explicit decisions;
exclude unrelated transcripts, private operational handoffs and stale instructions.

Codex reads the design and actual source, implements the agreed scope, runs the
appropriate checks, and returns changed files, results, limitations and any design
deviations. Gemini reviews the resulting diff and behavior against the acceptance
criteria. Codex resolves concrete findings. Keep one agent editing a given file
at a time; independent work should use separate branches/checkouts when needed.

Do not assume conversations, tool permissions or session state are automatically
shared between Codex and Gemini. The shared contract and repository are the
handoff mechanism. Update facts after verified outcomes, with one writer at a time.

Suggested contract for the next story:

```markdown
# Story: <user-visible outcome>
Status/date: <planned, implementing, review, verified>
Owner: Gemini design / Codex implementation
Workspace, branch and starting commit:
Problem and user scenario:
Scope and explicit non-goals:
Current behavior and relevant modules/tests:
Chosen design, alternatives and reasons:
Interfaces, data contracts and error behavior:
Constraints and decisions that must survive the handoff:
Acceptance criteria with observable examples:
Implementation steps, each yielding a testable result:
Validation commands and expected behavior:
Deployment/configuration needs, if any:
Open questions and assumptions:
Codex outcome: changed files, check evidence, deviations, remaining work
Gemini review: concrete findings, severity and resolution
```

Measure success by accepted functionality, elapsed time, manual interventions and
rework. A promising design review or provider SUCCESS is not completed software.
Start with one representative story. Simplify the workflow before extending the
custom harness further; the new roles alone cannot repair transport or permission
problems.

## 4. What failed in the previous collaboration

These findings come from the saved 27 September attempts, inspected on 28 September.

| Observation | Conclusion and lesson |
| --- | --- |
| Architecture review succeeded in 117.49 seconds after 15 file reads | Gemini contributed successfully to design; preserve that output as useful input |
| Both implementation runs ended at approximately 300 seconds with `print timeout after 5m0s with turn in progress` | The configured CLI deadline ended them; this is not proof Gemini could not implement the task |
| First implementation wrote four files before timing out | Four substantial modules/tests plus testing and correction did not finish within our five-minute task budget |
| A correction tried reading the exact safe-command wrapper and the gate denied it | Our permission setup was inconsistent with the assigned workflow |
| Corrected run read six files, then produced error events with no diagnostic message | The underlying cause remains unknown; do not invent a model-quality explanation |
| First implementation prompt was 26,632 bytes and allowed broad tracked-source reads | That run was not constrained by the standard 12 KB packet cap; more text alone was not the solution |
| Prompt included long historical/coordinator handoffs, but not the completed architecture review as a contract | Context must be coherent and task-relevant; copying everything can carry contradictory instructions |
| Runs used `gemini-3.7-flash-medium` through a custom Antigravity runner | This was not a controlled comparison with Magne's successful standalone Gemini setup |
| Final review never started after UI automation lost the VS Code window | This was an execution-environment failure, not another Gemini inference timeout |

Codex owned the orchestration and task breakdown and should own those mistakes.
Gemini's drafts also needed substantive corrections: the check phase initially
called `git fetch`, receipt/path validation was incomplete, and one test seam was
incorrect. Partial progress is useful but does not establish correctness.

Future supervision should distinguish startup/configuration failure, permission
denial, interrupted transport, useful incomplete progress, test failure and verified
completion. Size total budgets to the task and distinguish activity from inactivity.
Preserve resumable session IDs even when no final result arrives. These are
recommendations; the current runner still enforces a fixed deadline.

## 5. Deployment refactor: built locally, not released

Keep the separation requested by Magne:

1. `scripts/release_check.py` performs local Git scope checks, root/adapter/dashboard
   checks, builds and secret scans, then creates a checked receipt and source bundle.
2. `scripts/release_deploy.py --validate-only --receipt PATH` verifies the receipt,
   staged Git tree and artifact integrity and prints the deployment plan.
3. A separately authorized `scripts/release_deploy.py --release ...` publishes the
   checked source to GitHub, verifies CI for that commit, ships the existing bundle
   to Cloud Run, checks candidates, promotes, verifies production and rolls back
   on relevant failures. It does not rerun pytest/npm/Gitleaks or rebuild local assets.

`scripts/release_artifacts.py` binds hashes, modes, sizes and service bundles to
schema-v2 receipts. `scripts/release.py` is a compatibility wrapper; its old combined
`--release` invocation is rejected with replacement instructions. See
[DEPLOYMENT.md](DEPLOYMENT.md) and [LOCAL_DEPLOY_PROMPT.md](LOCAL_DEPLOY_PROMPT.md).

The current implementation still includes GitHub CI and runtime verification in
deployment. Those establish that the published commit and serving services are
correct; local code checks belong in the earlier check phase.

Saved final check: root tests, adapter tests, dashboard formatting/build/budgets
and Gitleaks passed. Codex independently validated the sealed input. After
integration, 123 focused release/harness tests passed in the primary workspace.
An earlier full root run passed 588 tests plus 41 subtests. These are historical
27 September results, not checks rerun during this documentation handoff.

Latest sealed check in the isolated checkout:

```text
.local/release-checks/260927-204109-6346/summary.json
tree: a4f84317ee91d734614054e1cebfb055e53def5c
integrity: 92f13fdedca01f7a3232a3c0c40baf0e674f4008d2bbcc3e90101246413658e6
contents: 197 source files and four dashboard assets
```

That receipt is bound to its exact checked tree. Documentation now being updated
in the primary workspace is outside that snapshot. Any newly staged payload needs
its own check receipt; do not reuse the old receipt to ship changed files.

## 6. Harness: retained implementation and known limits

| File | Purpose |
| --- | --- |
| `scripts/harness.py`, `harness_jobs.py` | Job creation, routing, attempts and review/completion records |
| `scripts/harness_context.py` | Immutable snapshots of the curated worker memo |
| `scripts/gemini_jobs.py`, `gemini_gate.py` | Bounded read-only Gemini packets and source gate |
| `scripts/gemini_collaborator.py`, `gemini_collaborator_gate.py` | Exact editable paths and named command wrappers |
| `scripts/gemini_worker.py` | CLI stream capture, deadline and result classification |
| `scripts/harness_release.py` | Saved deployment-evidence checks, not a live cloud query |
| `scripts/local_worker.py` | GPT-oss one-file/function draft helper |
| `scripts/workload.py` | Work attribution; counts do not prove token savings |

Existing improvements: up to 16 selected source files/excerpts for Gemini;
permission/profile file preparation before attempt allocation; temporary grants
restored after execution; non-target files hash-bound; command-only review jobs.
The separate edit/command smoke preflight succeeded after setup fixes. File setup
alone is not proof that every actual permission/tool path works.

Existing limits: combined packet 12 KB, curated memo 4000 UTF-8 bytes; default
Gemini timeout 120 seconds, maximum 300; restricted reads/writes/commands; visible
VS Code terminal guard; at most three Gemini attempts or two local attempts per
job; no automatic model switch, credit purchase or quota-resume loop.

These are constraints of the optional harness, not limits to impose on Gemini's
native dialogue, research or architecture work. Its routing still labels
`architecture`, `security`, `iam` and `release` as Codex tasks. That code has not
been changed by this handoff and does not override the new human-approved roles.
Do not use the legacy architecture route to launch Gemini's primary conversation.

## 7. Workspace and evidence map

Primary workspace: `/Users/magnelima/Workspace/AI-Coach`, branch
`release/lightweight-dark-dashboard`, HEAD `44afae7252fd887dce72ccdfbb329e9a327ce6ea`.
It contains mixed staged, unstaged and untracked work. Preserve it; do not reset,
stash, auto-stage or clean it. The previous index was preserved during integration.

Isolated checkout: `.local/release-physiology/source`, now on
`work/deployment-collaboration-v2`, HEAD
`3462024da3a7f8b414ed57d958181d9e22e8cf55`, with 19 staged refactor files.
The reviewed code changes were also applied to the primary working tree without
staging them. This handoff lives in the primary workspace; isolated docs may be older.

Useful local evidence references (inspect selectively; do not forward whole logs):

| Path relative to primary workspace | Meaning |
| --- | --- |
| `.local/collaboration-v2/gemini-design-review/review.md` | Completed Gemini architecture review; a proposal, not the final implementation |
| `.local/collaboration-v2/gemini-release-split-implementation-1/` | Prompt, streamed edits, gate log, timeout and result |
| `.local/collaboration-v2/gemini-release-split-correction-2/` | Wrapper-read denial and interrupted run |
| `.local/collaboration-v2/gemini-release-split-correction-2b/` | Corrected gate, six reads, error events and timeout |
| `.local/collaboration-v2/gemini-collaborator-preflight-5/verified-result.json` | Successful permission/edit/command smoke test |
| `.local/release-physiology/source/.local/release-checks/260927-204109-6346/summary.json` | Latest local sealed check; not deployment evidence |
| `.local/releases/260927-063319-c77f/summary.json` | Historical successful physiology deployment, original retained in isolated checkout |
| `.local/worker/README_HANDOFF.md` | Private operational coordinator history; do not paste into worker prompts |
| `.local/worker/context.md` | Curated bounded worker memo; eligible for new harness snapshots |

Pending job `7c8b4c2c213247b1b9f4a09f36eaed05` is stored in the isolated checkout
under `.local/worker/jobs/`. Its record is still `ready`, with zero attempts.
The previous instruction to run it automatically is superseded by this role switch.
Preserve the record; Gemini can review the refactor natively if it is the next
agreed task. Older wording review `9ef39467527842a5822cffd769e19551` remains deferred.
No relevant jobs were running when status was inspected for this handoff.

The new `harness.py status` only reads `.local/deployments/`; it currently reports
no saved deployment attempt. The successful old receipt is under `.local/releases/`.
That status difference neither invalidates the historical release nor proves live
health. Do not rewrite historical receipts to satisfy the new format.

## 8. Product state and next work

The physiology epic and workout-export source were published/deployed at commit
`3462024` on 27 September; PR 2 was open at the last check. The deployment refactor
was a workflow prerequisite, not a newly completed product story. No new product
story was selected and implemented during that refactor.

Saved open product work: inspect initial physiology migration/projection progress;
prepare missing OpenAI provider configuration for Quick Ride/Quick Run; verify a
real owner recommendation and download; treat physical Garmin import as separate
device acceptance. The source/export logic is already deployed, but paid generation
and real owner downloads were not verified. Use `README_CURRENT_CONTEXT.md`,
[PHYSIOLOGY.md](PHYSIOLOGY.md) and [QUICK_WORKOUT.md](QUICK_WORKOUT.md) for contracts.

Gemini should choose the next story with Magne instead of redoing completed work.
First review the split release workflow if needed; no further harness redesign is
required merely to start the new dialogue/design arrangement.

## 9. Persistent boundaries

Public source repository: `limux92/ai-coach`. Routine deployment covers the existing
backend and gateway while preserving configuration and IAM. Source publication is
not a deployment. Local check success is not production verification.

Secrets belong in Secret Manager. Never ask for key values in chat or include
credentials, personal training records, raw operational logs or private handoffs
in model packets/public source. Research uses public information unless Magne has
explicitly authorized the relevant private-data access.

Backend remains private behind Cloud Run IAM. Dashboard Firebase identity and MCP
gateway OAuth are different token boundaries; preserve the OAuth issuer/PKCE repair.
Missing telemetry is unknown, not zero. Do not use bootstrap deploy scripts that
replace runtime settings for a routine release.

The completed 27 September publication approval does not authorize a new payload,
IAM expansion, secrets, force-push, PR merge or unrelated infrastructure changes.
Prepare exact payload/destination/commands first, then seek any missing approval
once. This handoff changes ownership and documentation only.

## Paste into Gemini

```text
Continue AI-Coach at /Users/magnelima/Workspace/AI-Coach.
Read docs/GEMINI_WORKFLOW_HANDOFF.md, AGENTS.md and README_CURRENT_CONTEXT.md.
You now lead my dialogue, requirements, architecture, design and research.
Codex owns implementation, tests, scripts, automation and authorized execution.
Use a shared story design/acceptance document for handoffs. Preserve all existing
staged, unstaged and untracked work. Do not auto-run old worker jobs or redeploy.
Start with a concise understanding of the current state and help me choose the
next story. Treat historical test and production results as dated evidence.
```
