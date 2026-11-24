# Story: OpenAI Quick Workout Activation & Owner Download Validation

- **Status / Date**: Planned / Specifying (2026-09-28)
- **Owner**: Gemini (Architecture / Design) & Codex (Implementation / Execution)
- **Workspace**: `/Users/magnelima/Workspace/AI-Coach`
- **Branch**: `release/lightweight-dark-dashboard`
- **Target Service**: `ai-coach-chat` (Europe-north1, project `magne-ai-coach-20260915`)

---

## 1. Problem & User Scenario
Magne wants on-demand personalized daily workouts directly from the dashboard:
1. **Quick Ride · Zwift**: Cycling session and downloadable `.zwo` file scaled to Zwift FTP.
2. **Quick Run · Garmin**: Running session and downloadable `.fit` workout file with lap-press open warm-up/cool-down and timed interval blocks.

The backend schemas (`quick_workout_schema.py`, `running_workout_schema.py`), binary FIT encoder (`garmin_workout.py`), and dashboard UI buttons (`quick-workout.js`) are **already deployed in production** (commit `3462024`). However, the feature is inactive (returns 503) because runtime settings `OPENAI_API_KEY` (secret) and `QUICK_WORKOUT_MODEL` are not yet bound to Cloud Run.

---

## 2. Scope & Boundaries

### In Scope
- Identifying or creating the Secret Manager secret for the OpenAI API key (secret name only; never paste or ask for key values in chat).
- Selecting an appropriate model supporting OpenAI Structured Outputs (e.g. `gpt-4o-mini` or `gpt-4o`).
- Cloud Run service configuration update for `ai-coach-chat` binding the secret to env `OPENAI_API_KEY` and setting `QUICK_WORKOUT_MODEL`.
- Live validation of:
  - Authenticated owner POST `/dashboard/api/quick-workout` (cycling) and `.zwo` export.
  - Authenticated owner POST `/dashboard/api/quick-workout/run` (running) and `.fit` binary download.
- Verification of binary FIT compliance (sport: running, lap-press bookends, timed intervals).

### Explicit Non-Goals
- Uploading to Garmin Connect (downloadable files only).
- Bypassing or modifying Firebase owner authentication or Cloud Run IAM.
- Exposing OpenAI keys or personal training data in prompts or public source.
- Physical watch synchronization (separate device test by Magne).

---

## 3. Data Contracts & Safety Invariants

### Runtime Configuration
- Secret: `OPENAI_API_KEY` mounted from Secret Manager (e.g. `openai-api-key:latest`).
- Env var: `QUICK_WORKOUT_MODEL="gpt-4o-mini"`.

### Request & Rate Boundaries
- Both sports share:
  - In-flight request lock (1 concurrent request per process).
  - Rate limiting (minimum 60s interval between calls).
  - Quota cap (maximum 10 requests per UTC day per process).
  - Cache TTL (10 minutes, isolated by sport).
- Payload limit: Context <= 24 KB, model output <= 3000 tokens.
- Parameter restrictions: Empty POST body; no query parameters or custom prompts accepted.

---

## 4. Acceptance Criteria

1. **Gateway Configuration**:
   - `ai-coach-chat` Cloud Run revision has access to the OpenAI key secret and defines `QUICK_WORKOUT_MODEL`.
2. **Cycling Quick Workout Validation**:
   - Authenticated POST `/dashboard/api/quick-workout` returns HTTP 200 with `sport: "cycling"`, rationale, and `.zwo` file content (or rest recommendation).
3. **Running Quick Workout Validation**:
   - Authenticated POST `/dashboard/api/quick-workout/run` returns HTTP 200 with `sport: "running"`, title, rationale, and base64-encoded FIT workout.
4. **FIT Binary Integrity**:
   - Decoded FIT file verifies sport = `running`, warm-up/cool-down step duration = `open` (ends on LAP press), and main set intervals are strictly timed.
5. **Privacy & Security**:
   - Zero credentials or personal workout records appear in logs, test suites, or chat output.

---

## 5. Execution Plan (Path 1 Multi-Agent Workflow)

1. **🔷 [Gemini]**:
   - Formalize story contract (this document).
   - Prepare mock provider fixtures and test specifications.
2. **🟪 [GPT-oss]**:
   - Draft parameterized error-handling unit tests for `quick_workout_provider.py` (e.g., rate limits, invalid JSON, provider timeout).
3. **🟩 [Codex]**:
   - Review and integrate GPT-oss draft into `adapters/mcp/tests/test_quick_workout_flow.py`.
   - Run adapter and dashboard tests.
   - Prepare the Cloud Run configuration deployment patch for `ai-coach-chat`.
4. **Magne**:
   - Created secret version in Secret Manager (`openai-api-key:latest`).
5. **🟩 [Codex] / 🔷 [Gemini]**:
   - Updated Cloud Run service `ai-coach-chat` with additive `--update-secrets` and `--update-env-vars`.
   - Verified revision `ai-coach-chat-r-3462024d-260927-063319-c77f` is deployed and serving 100% traffic.

---

## 6. Verified Outcomes (2026-09-28)

- **Secret Manager**: `openai-api-key:latest` enabled and bound to `ai-coach-chat@magne-ai-coach-20260915.iam.gserviceaccount.com` (`roles/secretmanager.secretAccessor`).
- **Cloud Run Service**: `ai-coach-chat` revision deployed and serving 100% traffic in `europe-north1`.
- **Environment State**:
  - `OPENAI_API_KEY`: Mounted from secret `openai-api-key:latest`.
  - `QUICK_WORKOUT_MODEL`: `gpt-4o-mini`.
  - All pre-existing Firebase and backend parameters preserved intact.
- **Security Probing**:
  - Unauthenticated POST to `/dashboard/api/quick-workout` returns HTTP 401 with strict security headers, confirming gateway protection is active.
- **Next Verification**:
  - Magne logs in on dashboard and clicks "Quick Ride · Zwift" or "Quick Run · Garmin" to test end-to-end browser download.
