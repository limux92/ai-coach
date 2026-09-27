# Lightweight dashboard

Plain JavaScript modules, HTML templates and feature stylesheets, inspired by
JustInterval's simple dark surfaces. Firebase Auth is the only runtime package;
there is no UI framework, chart library, web font or decorative image download.
Vite and Prettier run during development/build, not in Cloud Run.

## Find the right file

Paths below are relative to `dashboard/src/`.

| Change | Source |
| --- | --- |
| Dark colors, typography defaults | `styles/tokens.css` |
| Shared controls, focus, loading/error states | `styles/base.css` |
| Navigation and responsive layout | `styles/layout.css`, `views/shell.js` |
| Monthly metrics and recent sessions | `views/overview.js`, `views/recent.js`, `styles/overview.css` |
| Trend and heart-rate zone charts | `views/charts.js`, `styles/overview.css` |
| Month/week calendar | `views/calendar.js`, `styles/calendar.css` |
| Workout details and sample chart | `views/workout.js`, `views/samples.js`, `styles/workout.css` |
| Sign-in screen | `views/login.js`, `styles/login.css` |
| App startup, session and initial data loading | `main.js` |
| Authenticated API and list pagination | `api.js` |
| Cycling/Zwift and running/Garmin recommendations | `quick-workout.js` |
| Clicks, calendar navigation, keyboard controls | `events.js` |
| Workout/sample loading and focus restoration | `workout-controller.js` |
| Dates, aggregates and filtering | `data.js`, `selectors.js` |
| Icons and shared markup helpers | `ui.js` |
| Chat authorization flow | `connect.js` |

`style.css` imports the stylesheets; edit the feature file, not the import list.
Keep missing metrics unknown, historical zone definitions separate and source
attribution visible. Keep authentication changes with the primary assistant.

## Develop and check

From `dashboard/`:

```sh
npm ci
npm run dev     # http://127.0.0.1:5174/dashboard/
npm run format # format readable source
npm run check  # format check, Node tests, production build, size budgets
```

Development still needs the authenticated `/dashboard/config` and API service;
the Vite server alone is not a working data demo. Do not add an authentication
bypass or personal fixtures to production. Tests use Node's built-in runner in
`test/`. Production output goes to ignored `../adapters/mcp/static/dashboard`.
The existing Python service serves these static files; building is not deploying.

## Runtime contract

Imported records remain read-only. Refresh reloads the view without starting a
source sync. Quick Workout makes an authenticated POST to generate an in-memory
cycling or running recommendation and matching Zwift or Garmin FIT download; it does not write training records or plans.
See [Quick Workout](../docs/QUICK_WORKOUT.md) for runtime setup and model data flow.
Firebase public web configuration comes from `/dashboard/config`; Google sign-in
uses session storage. Training records are not persisted in browser storage.
`/dashboard/api` accepts signed ID tokens for the configured owner.
`/dashboard/connect` handles explicit chat-client OAuth consent; see the
[Firebase runbook](../docs/FIREBASE_AUTH.md). Local authenticated development
also needs a registered callback.

Workout and plan lists follow `next_cursor`; sample pages follow `next_offset`.
The athlete timezone determines today, while date-only calendar arithmetic uses
UTC. Virtual distances and partial/downsampled profiles are labeled. Blank dates
do not imply rest. Summary-only records retain their sample-availability warning.
Keyboard support includes visible focus, a skip link, dialog focus trapping and
Escape to close. Dense tables and calendars scroll within their panels on mobile.

## Size budgets

Keep each source JS/CSS file at most 12,000 bytes and each line at most 240
characters. Production budgets total every emitted asset: JavaScript at most
50,000 gzip bytes and CSS at most 6,000. `npm run check` and CI enforce these.
Readable source and minified build output serve different purposes. Formatting
alone does not reduce Cloud Run CPU or memory usage.

## Small local-model tasks

From the repository root, once the relevant source is tracked:

```sh
.venv/bin/python scripts/local_worker.py \
  'Make the background slightly darker. Preserve variable names and readable text. Return this file only.' \
  --task refactor --file dashboard/src/styles/tokens.css --label 'Adjust dark palette'
```

For a function, use `--file path:START:END` (inclusive). The helper accepts only
tracked source, limits the total prompt to 12 KB, uses 16K context, defaults to
2,048 output tokens and a 120-second timeout, and keeps the model loaded for five
minutes afterward. Use one request at a time. Give concrete acceptance criteria;
do not attach lockfiles, generated output, secrets or training records.

Review the saved `.local/worker/` draft before applying it. Run focused checks,
allow at most one correction, then narrow the task if needed. An incomplete
draft exits with code 2 and must not be applied. These limits make small tasks
more manageable; they do not guarantee every GPT-oss response is correct.
