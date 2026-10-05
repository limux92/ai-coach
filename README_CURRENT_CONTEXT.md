# AI Coach — Current Context & Handoff

**Updated: 5 October 2026**
**Active Branch:** `release/lightweight-dark-dashboard` (HEAD `e842c9f`)
**Production Release:** `e842c9feb212039221fd8d0ddaeddcffeaa91b0b` (`release/lightweight-dark-dashboard`), serving revisions at 100%.
**Active Revisions:** `ai-coach-sync-r-e842c9fe-261005-124238-4d78`, `ai-coach-chat-r-e842c9fe-261005-124238-4d78`

---

## 1. Executive Summary & Where We Are

1. **Role Split (Mandated)**:
   - **Gemini**: Requirements lead, architecture, Google Cloud multi-user platform setup, sports-science algorithm design, and code review.
   - **Codex**: Implementation, integration, test suites, automation, and verification.
   - **Local Worker (Qwen 3.8 / `qwen3.8:27b-q4_K_M` on Ollama)**: Offloads heavy computation and drafts bounded pure functions, math algorithms, and unit tests via `scripts/local_worker.py`.

2. **Completed & Verified (GoldenCheetah Feature 1 / PMC)**:
   - Continuous Performance Management Chart (PMC) is implemented and verified in [`dashboard/src/views/pmc.js`](dashboard/src/views/pmc.js), [`dashboard/src/data.js`](dashboard/src/data.js), and [`dashboard/src/styles/overview.css`](dashboard/src/styles/overview.css).
   - Calculates 84-day rolling EWMA fitness ($\text{CTL } \tau=42$), fatigue ($\text{ATL } \tau=7$), and form ($\text{TSB} = \text{CTL} - \text{ATL}$) with form status badges.
   - Removed deprecated Quick Workout leftovers from `dashboard/src/views/shell.js` and `dashboard/src/styles/overview.css`.
   - **Quality Gates**: All 14 dashboard tests pass (`npm --prefix dashboard run check`), Vite build succeeds, and gzip budgets pass (JS 45.5 KB / 50 KB, CSS 4.79 KB / 6 KB).

3. **In-Flight Priorities**:
   - **Priority 1: Critical Power (CP) Visuals in Dashboard**: Mean Maximal Power (MMP) curve with Critical Power ($CP$) and anaerobic work capacity ($W'$) hyperbolic overlay.
   - **Priority 2: Google Cloud Multi-User Scaling (3-Step Sequence)**: Database multi-tenancy $\rightarrow$ Registration UI $\rightarrow$ Payment paywall.

---

## 2. Antigravity Permissions: Root Cause & Permanent Fix

### Why Popups / "Operation Not Permitted" Occurred:
1. **No Workspace Binding at Session Launch**: The conversation was initialized in "no-workspace" scratch mode. On macOS, the Antigravity sandbox strictly denies traversal to subdirectories outside the initial workspace boundary.
2. **Default Tool Execution Policy**: Antigravity defaults to prompting before `write_file`, `replace_file_content`, and unsandboxed terminal commands unless the execution policy is explicitly set to `always-proceed`.
3. **Project Scoping**: Magne configured project `8b32b124-7a43-4d79-a5e7-1e3b80c0f765.json` (AI-Coach), but old conversation threads retain their original scratch permissions unless updated globally.

### How to Permanently Disable Approval Prompts:
To ensure the AI never prompts for file writes or commands again:

1. **Global App Settings (Antigravity 2.0 / IDE)**:
   - Open **Settings** (`⌘ ,` or gear icon).
   - Set **Tool Execution Policy** $\rightarrow$ **`always-proceed`** (or "Auto-approve all actions").
   - Set **Non-Workspace File Access** $\rightarrow$ **`allow`**.
   - Set **Terminal Sandbox Mode** $\rightarrow$ Disabled or set to trust workspace folder.
   - In the chat interface footer, verify the execution mode toggle is switched from *Review Mode* to *Turbo / Auto-run*.

2. **Verified Allow Grants in `~/.gemini/config/config.json`**:
   The following grants are now active:
   - `write_file(/Users/magnelima/Workspace/AI-Coach)`
   - `read_file(/Users/magnelima/Workspace/AI-Coach)`
   - `nonWorkspaceFileAccessPolicy: AGENT_SETTING_POLICY_ALLOW`

3. **Start Conversations Inside the Project**:
   In Antigravity's left sidebar, click **Projects** $\rightarrow$ select **AI-Coach** $\rightarrow$ **New Chat**. This guarantees the conversation runs with full project-scoped permissions from turn 1.

---

## 3. Roadmaps for Next Steps

### Roadmap A: CP Visuals & PMC in Dashboard (Completed, Released & Deployed)
* **Status**: **RELEASED & DEPLOYED IN PRODUCTION**
  - Commit: `03b44584963c34d6c1bd169dd5eb548033ffb8bd` on `release/conversational-coach` (GitHub PR #3 updated).
  - Cloud Run Revisions: `ai-coach-sync` & `ai-coach-chat` serving `r-03b44584-261002-102621-d11d` at 100% traffic.
  - Production URL: https://aiworkoutbuilder.app/dashboard/
  - Deployment Receipt: `.local/deployments/261002-102621-d11d/summary.json`.
  - Check Receipt: `.local/release-checks/261002-102524-7dbe/summary.json` (20/20 dashboard tests, 583+41 pytest tests, 203 MCP tests, builds, budgets, Gitleaks scans).
* **Delivered & Fixed**:
  - **Critical Power data pickup**: Dashboard `loadData()` now queries `/context` in parallel with workouts. When physiology model CP is null/missing, CP and $W'$ are dynamically calculated directly from the athlete's actual Mean Maximal Power (MMP) curve (using 5m/20m Monod/Coggan model or 20m 95% threshold) instead of defaulting to a fixed 250W.
  - **PMC visual display**: Added complete CSS styles for `.pmc-line` (CTL fitness blue, ATL fatigue amber, TSB form teal), optimal training zone shading, zero line, grid lines, daily load bars, and interactive legend in [`dashboard/src/styles/overview.css`](dashboard/src/styles/overview.css).
  - **Fitness / Fatigue Warmup**: Added 42-day EWMA warm-up and 126-day lookback in [`dashboard/src/data.js`](dashboard/src/data.js) so CTL and ATL accurately reflect the athlete's training load history instead of ramping up from 0.
  - Verified 20/20 unit tests, bundle budgets (<50KB JS, <6KB CSS), sealed receipts, zero-downtime canary deployment, and live HTTP 200 verification.

### Roadmap B: Google Cloud Multi-User Platform (Fully Implemented, Released & Deployed)
* **Status**: **100% IMPLEMENTED, RELEASED & DEPLOYED IN PRODUCTION**
  - **Step 1: Database (Multi-Tenant Isolation)**:
    - Partitioned Firestore collections under `users/{userId}/*` (`workouts`, `wellness`, `sync_state`, `physiology_models`, `credentials`).
    - Authoritative [`firestore.rules`](firestore.rules) enforcing strict user-scoped isolation (`request.auth.uid == userId`).
    - [`src/ai_coach/storage.py`](src/ai_coach/storage.py) `Store.for_user(user_id)` partition routing and `user_scope_middleware` via `X-User-Id`.
  - **Step 2: Register Function & Athlete Onboarding**:
    - [`src/ai_coach/main.py`](src/ai_coach/main.py) `POST /v1/user/register` & `GET /v1/user/profile` with initial `status: "pending_payment"`.
    - Dashboard Google Sign-In with Sign In / Register toggle and dedicated onboarding screen.
  - **Step 3: Payment Wall, Terms of Sale & Vipps Recurring Integration**:
    - Norwegian Terms of Sale (salgsbetingelser) with mandatory explicit consent at registration/checkout.
    - [`src/ai_coach/billing.py`](src/ai_coach/billing.py) Vipps Recurring agreement v3 (199 NOK/mnd) with direct return activation (`pending_payment` -> `active`).
    - Vipps webhook listener (`POST /v1/webhook/vipps`) handling cancellation/stop (`active` -> `inactive`).
    - Stripe fallback subscription & HMAC-SHA256 signature verification listener (`POST /v1/webhook/stripe`).
    - Paywall enforcement blocking unactivated accounts from viewing coaching and sync data.
  - **Step 4: Multi-Tenant Intervals.icu Credentials & Background Sync**:
    - Credentials stored securely under `users/{userId}/credentials/intervals` (`POST/GET /v1/user/intervals-credentials`).
    - Scoped background synchronization runner [`src/ai_coach/sync.py`](src/ai_coach/sync.py) (`run_sync_for_user`, `run_multi_tenant_sync`) with rate budgeting.
    - Click-to-connect Intervals modal in dashboard shell with athlete ID and API key inputs.
  - **Step 5: Cloud Scheduler & Verification**:
    - Background sync runner hooked into `POST /internal/sync` and `POST /internal/sync/multi-tenant` so Cloud Scheduler jobs automatically sync active subscribers.
  - **Step 6: Owner Payment Wall Bypass & Root Partition Routing**:
    - Identified owner via UID (`N0lThhWrg4YfdoYwHjJbvl5swmk2`), email (`magne@fam-lima.net`), and `X-Is-Owner`.
    - Auto-healing of existing owner profiles from `pending_payment` to `active` + `role: owner`.
    - Store partition bypass routes owner to root collections, connecting dashboard to root workouts and physiology models.
  - **Step 7: Smart Initial Month Initialization & Recent Sessions Fallback**:
    - Default dashboard month/week on initial load automatically jumps to the latest workout month (September 2026) when the current month is empty, so athletes immediately see their workouts.
    - Recent sessions table displays latest historical sessions with fallback notice instead of an empty banner.
    - Intervals modal displays active "Tilkoblet" status and auto-sync notice for configured users.
  - **Step 8: Dashboard Event Routing & Shell Action Binding**:
    - Fixed unbound `shell` action in `bindEvents` in `dashboard/src/main.js`, resolving button click failures for sport filters ("Running", "Cycling", etc.), "View calendar" and sidebar navigation, and Intervals modal.
    - Added defensive default for `shell` in `dashboard/src/events.js`.
    - Added unit test suite `dashboard/test/events.test.js` covering all navigation and filter click handlers.
  - **Automated Verification & Gates**:
    - Standalone E2E verification test suite [`scripts/test_onboarding_billing.py`](scripts/test_onboarding_billing.py) passing all 9/9 checks in 0.04s.
    - 644 backend tests, 205 adapter tests, 24 dashboard tests passing.
    - Bundle budget passed: JS gzip 49,091 / 50,000 bytes; CSS gzip 5,757 / 6,000 bytes.
    - Deployed to Google Cloud Run (`ai-coach-sync` & `ai-coach-chat`) at 100% traffic; live at `https://aiworkoutbuilder.app/dashboard/`.
