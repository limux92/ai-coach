# AI Coach — Current Context & Operational Baseline

**Last Updated:** 6 October 2026  
**Active Branch:** `release/lightweight-dark-dashboard` (HEAD `6550f53`)  
**Production Release:** `e842c9feb212039221fd8d0ddaeddcffeaa91b0b` (`release/lightweight-dark-dashboard`), serving revisions at 100% traffic.  
**Active Cloud Run Revisions:** `ai-coach-sync-r-e842c9fe-261005-124238-4d78`, `ai-coach-chat-r-e842c9fe-261005-124238-4d78`  
**Live Production URL:** [https://aiworkoutbuilder.app/dashboard/](https://aiworkoutbuilder.app/dashboard/)

---

## 1. Operating Roles & Responsibilities

Per `AGENTS.md` and the 28 September 2026 mandate:
* **Gemini (Lead)**: Product dialogue, requirements management, system architecture, sports-science algorithm design, and independent code review.
* **Codex (Executor)**: Application & infrastructure implementation, scripts, automation, test execution, and deployment verification.
* **Local Worker (Qwen 3.8 / `qwen3.8:27b-q4_K_M` on Ollama)**: Bounded pure mathematical algorithms, isolated parsing drafts, and unit test generation.
* **Magne (Owner)**: Strategic product direction, commercialization decisions, and release authorization.

---

## 2. Current Production Baseline

All foundational platform milestones across Roadmap A (Analytics) and Roadmap B (Multi-User Infrastructure) are completed, verified, and running live in Google Cloud Run:

1. **Lightweight Dark Dashboard:**
   * Pure vanilla ES modules, responsive semantic HTML, CSS tokens (`tokens.css`), system fonts.
   * Total gzip payload: JavaScript 49.09 KB (budget <= 50 KB), CSS 5.76 KB (budget <= 6 KB).
   * Verified sports filtering (Cycling, Running, Other sports, All), Calendar month/week navigation, and recent sessions fallback.

2. **Physiological Analytics Engine:**
   * 84-day rolling EWMA Performance Management Chart (CTL $\tau=42$, ATL $\tau=7$, TSB $= \text{CTL} - \text{ATL}$) with 42-day historical warm-up.
   * Dynamic Critical Power & MMP curve calculation with fallback estimation from workout power profiles.

3. **Multi-Tenant Google Cloud Architecture:**
   * Firestore partitioned under `users/{userId}/*` with strict isolation enforced by `firestore.rules`.
   * Owner payment wall bypass and automatic profile healing for `magne@fam-lima.net` (`N0lThhWrg4YfdoYwHjJbvl5swmk2`).
   * Automated Intervals.icu background synchronization runner with upstream rate budgeting.
   * Subscription onboarding flow with Vipps MobilePay recurring agreement v3 (199 NOK/mo) and Norwegian Terms of Sale.

---

## 3. Active Roadmap & Backlog

All upcoming epics, feature stories, priorities, and acceptance criteria are tracked in:
👉 **[`PRODUCT_BACKLOG.md`](PRODUCT_BACKLOG.md)**

Current planning focus areas:
1. **Critical Power Verification (PHY-01):** Validating CP pickup against real athlete data and eliminating fallback discrepancies.
2. **Dashboard In-App Chat Interface (CHAT-01):** Bringing conversational coaching directly into the web app.
3. **Interactive Workout Builder (PLAN-01 & PLAN-02):** Visual interval builder with push sync to Intervals.icu calendar.
4. **Vipps Production Commercialization (BIZ-01):** Transitioning recurring billing to production merchant credentials.
