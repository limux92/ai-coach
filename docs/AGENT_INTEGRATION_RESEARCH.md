# Agent integration research — 25 September 2026

## Decision

Use Codex as the coordinator, local GPT-oss for small application drafts and
Gemini/Antigravity for infrastructure reviews and proposals. Preserve task state in
the existing local harness. Use fresh worker contexts with a bounded brief, source
selection, acceptance criteria and an inspectable result. Codex verifies completion.

## Official platform findings

- [VS Code 1.139 release notes](https://code.visualstudio.com/updates/v1_139):
  released September 23, with 1.139.1 available. The Agent Host runs harnesses in a
  dedicated process using AHP. This release adds remote Dev Container sessions,
  faster session catalogs and session/chat UI improvements. Multiple chats retain
  separate contexts. These notes do not describe automatic history sharing between
  arbitrary Codex and Antigravity extensions; our CLI bridge is a separate layer.
- [Antigravity headless interface](https://antigravity.google/docs/cli/headless/):
  structured stream events expose initialization, tools and the terminal result.
  A new invocation starts fresh unless a conversation is explicitly resumed.
  Use a coordinator-owned deadline and validate tool outcomes as well as exit code.
- [Antigravity hooks](https://antigravity.google/docs/hooks/): workspace
  `.agents/hooks.json` can gate tool calls before execution. Our packet gate allows
  the exact supplied source read and denies other tools. An observed CLI run lists
  the full tool catalog at initialization even with a custom main-agent profile;
  the hook audit and completed tool events therefore verify the effective boundary.
- [Custom subagents](https://antigravity.google/docs/subagents/): support separate
  contexts, scoped tools and workspace options within Antigravity. They do not
  connect this Codex conversation automatically. The documentation also describes
  a known hang risk from invalid tool names, reinforcing the need for deadlines.

## Community implementations examined

- [mcp-agents](https://github.com/thomaswitt/mcp-agents) wraps multiple provider CLIs
  as MCP tools. Its Gemini provider uses Antigravity `agy` with a sandbox and an
  explicit timeout. Useful pattern: a narrow callable adapter with bounded input
  and output. We use the installed CLI directly and keep work visible in VS Code.
- [Orkestra](https://github.com/andyyaro/orkestra) describes a coordinator with
  provider adapters, isolated workspaces and enforced orchestration rules. Useful
  pattern: separate agent proposals from the code that decides whether checks
  passed. We adopted that distinction through job states and release receipts.

These repositories were read as design references, not installed or independently
audited. Adding another full orchestrator would duplicate our existing local worker
and approval workflow. Native AHP integration can be reconsidered when a supported
adapter fits both providers; this implementation does not claim to provide one.

## Operational verification

The live setup checked was VS Code 1.139.1, Antigravity extension 1.5.0 and CLI
1.2.11. Native file reads/writes worked in a harmless probe. A headless command was
denied even though the CLI reported SUCCESS and an empty final answer. The harness
now rejects that combination. A fresh Gemini 3.7 Flash Medium packet review then
completed with a recorded source read and a nonempty, source-grounded answer.
An additional live boundary test allowed the disposable source read, denied a
requested temporary file write, verified the file was absent and recorded failure.

Keep private probe logs, conversation identifiers and release details under
ignored `.local/worker/`. These observations do not establish the cause of an older
conversation crash or prove current Cloud Run access. The verified automated Gemini
path is a bounded review worker; cloud execution retains its separate reviewed flow.
