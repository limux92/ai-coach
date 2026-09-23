# Training dashboard

A responsive, read-only training dashboard and calendar served at `/dashboard/` by the existing MCP adapter. Production includes no synthetic training data or private credentials. Firebase web configuration is public configuration, not a training-data credential.

## Build and test

```sh
cd dashboard
npm ci
npm test
npm run build
```

The Vite build writes self-hosted assets to `../adapters/mcp/static/dashboard/`. Build before deploying the MCP adapter. Dependencies are pinned and the lockfile is committed with this source.

`npm run dev` serves the source on `127.0.0.1:5174`. A local gateway/config endpoint and registered callback are required for authenticated development; standalone Vite does not bypass authentication.

## Runtime contract

The page obtains Firebase web settings from `/dashboard/config` and signs in with Google. Firebase sessions use session storage and are cleared on sign-out; training data is not persisted in browser storage. The API accepts only signed ID tokens for the configured owner. `/dashboard/connect` handles explicit OAuth consent for the chat client. See [Firebase authentication](../docs/FIREBASE_AUTH.md).

Authenticated reads go to `/dashboard/api`. Workout and plan pages follow `next_cursor`; sample pages follow `next_offset`. Date arithmetic uses the configured athlete timezone for today and UTC arithmetic for date-only calendars, including leap days and year boundaries.

## Views and data semantics

- Monthly overview: duration, distance, completed sessions, provider-estimated training load, 12-week volume chart, historical HR zones, recent sessions, activity-day calendar.
- Monday-first month/week calendar: completed and planned sessions, weekly totals, sport filters, direct month picker, Today/previous/next navigation.
- Workout drawer: source identity, available metrics, notes, lap count, HR zones, and paginated heart-rate/power samples. Partial and downsampled profiles are explicitly labeled.
- Different HR boundary definitions and sports remain separate. Missing measurements remain unknown; partial metric coverage is stated. Virtual distances are labeled. Empty dates do not imply rest. Large archived FITs show the backend's summary-only sample limitation.

The dashboard displays already imported records. It does not create workouts or write plans, and the refresh button reloads the view without initiating a source sync.

Keyboard support includes visible focus, a skip link, dialog focus trapping, Escape to close, and native month/select controls. Layout adapts to mobile; the dense calendar scrolls horizontally on narrow screens.
