# Story CHAT-01: In-App Conversational Coach Drawer

**Status:** Ready for Implementation  
**Epic:** Epic 2: Conversational AI Coach Interface  
**Author/Lead:** Gemini (Requirements & Architecture Lead) in alignment with Magne  
**Executor:** Codex (Implementation, Integration & Verification)  
**Target Release:** Roadmap Milestone CHAT-01  

---

## 1. Outcome & Objective

Provide athletes with an omnipresent, intelligent, and context-grounded conversational coach directly inside the web dashboard (`https://aiworkoutbuilder.app/dashboard/`).

Rather than relying on third-party chat clients or external tools, athletes can converse directly with the AI Coach in a slide-over drawer while viewing their Performance Management Chart (PMC), Critical Power curves, and training calendar.

---

## 2. Requirements & Product Decisions

Grounded in the alignment interview with Magne:

1. **UI Presentation:**
   - A floating coach action button (FAB) in the dashboard bottom-right corner.
   - Clicking toggles a responsive slide-over drawer on the right edge of the viewport.
   - Clean dark-theme design matching existing `tokens.css` styling and system fonts.
   - Lazy-loaded via dynamic ES module `import('./views/chat.js')` so the initial bundle size strictly respects budgets ($\le$ 50,000 bytes JS gzip; $\le$ 6,000 bytes CSS gzip).

2. **Athlete Coaching Focus & Goal Note (100 Words Max):**
   - Directly accessible inside the drawer header via an expandable "Goal & Focus" accordion.
   - Textarea with a live word counter (e.g. `45 / 100 words`).
   - Hard cap at 100 words enforced both client-side and server-side.
   - Autosaves on blur or edit to Firestore (`users/{userId}/profile/goal` or `users/{userId}`).

3. **Conversation Persistence & Sliding Context:**
   - Single continuous coaching thread per athlete stored in Firestore under `users/{userId}/chat_history`.
   - On drawer open, loads recent history (sliding window of last 20 messages).
   - Preserves continuity across devices and sessions.

4. **Real-Time Token Streaming (SSE):**
   - Gateway endpoint `POST /dashboard/api/chat/stream` proxies authenticated streaming requests to backend.
   - Transports tokens via Server-Sent Events (`text/event-stream`).
   - Frontend renders incoming markdown chunks in real-time with an active typing indicator.

5. **Physiological Context Grounding:**
   - Pre-injects:
     - Core system prompt ([`AI_COACH_SYSTEM_PROMPT.md`](../AI_COACH_SYSTEM_PROMPT.md))
     - Athlete physiological profile & 42-day rolling context (`/v1/context`)
     - Athlete's 100-word coaching goal note
     - Recent chat history
   - Bounded prompt injection to maintain deterministic sports-science accuracy without raw FIT time-series bloat.

6. **Token Metering & Owner Bypass:**
   - Token usage (input and output) recorded per turn in Firestore under `users/{userId}/token_usage`.
   - Owner UID `N0lThhWrg4YfdoYwHjJbvl5swmk2` (`magne@fam-lima.net`) is completely bypassed from limits and billing.
   - Non-owner athletes have usage tracked for quota enforcement and billing.

---

## 3. Architecture & Data Contracts

### 3.1 Backend Data Contract (`src/ai_coach/`)

#### Endpoints:
- `GET /v1/user/goal`: Retrieves the athlete's 100-word goal note.
- `POST /v1/user/goal`: Validates ($\le$ 100 words) and stores the goal note in Firestore.
- `GET /v1/chat/history`: Retrieves recent messages (`limit=20`) for the authenticated user.
- `POST /v1/chat/stream`: Initiates SSE stream invoking Gemini with pre-injected context and stream response chunks.

### 3.2 Gateway Routing (`adapters/mcp/src/ai_coach_mcp/dashboard.py`)

- Proxies authenticated requests from dashboard to private backend.
- Enforces user token verification (`OwnerTokenVerifier` with multi-tenant UID mapping).
- Supports SSE streaming response without buffering.

### 3.3 Dashboard Client Architecture (`dashboard/src/`)

- `main.js` / `events.js`: Attaches event listener to `#coach-fab`.
- `views/chat.js` (Dynamic Chunk):
  - Renders drawer markup (Header, Goal Accordion, Message List, Input Bar).
  - Handles SSE stream consumption via standard browser `fetch` and `ReadableStream`.
  - Lightweight markdown formatting (bold, italics, lists, code blocks).
- `styles/chat.css`: Responsive slide-over styles adhering to `tokens.css`.

---

## 4. Acceptance Criteria

1. **FAB & Drawer Toggle:** Clicking the coach button opens the right-hand slide-over drawer; closing or pressing Escape smoothly closes it.
2. **100-Word Goal Note:** Athletes can view and edit their goal note in the drawer header; exceeding 100 words is blocked with feedback; changes persist to Firestore.
3. **Chat Persistence:** Chat messages persist in Firestore; opening the drawer reloads recent conversation history.
4. **Streaming SSE:** Chat queries stream tokens in real-time with a typing indicator, rendering formatted responses without page reloads.
5. **Context Grounding:** Responses reference the athlete's actual metrics (CP, CTL, ATL, TSB, recent workouts) and respect the 100-word goal.
6. **Token Metering & Bypass:** Token consumption is recorded per athlete; owner (`magne@fam-lima.net`) is zero-rated and unblocked.
7. **Budget Compliance:** Running `npm --prefix dashboard run check` verifies JS gzip $\le$ 50,000 bytes and CSS gzip $\le$ 6,000 bytes for the initial load.
8. **Test Coverage:** Full test coverage across backend (`tests/test_chat.py`), MCP gateway, and frontend (`dashboard/test/chat.test.js`).
