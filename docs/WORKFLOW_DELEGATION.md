# Gemini, Codex and Qwen workflow

Updated 1 October 2026 at Magne's request. `AGENTS.md` makes the routing decision
below part of every prompt in this repository. These are coordinator instructions,
not a background process or an automatic connection between chat applications.

## Every prompt

1. Read the request and relevant current story. Preserve existing work.
2. State a short split: who owns design/review, implementation/verification and
   any useful local draft. For a simple question, state a direct response with no
   worker run. Continue an existing split for steering/status messages.
3. Give each substantial task one owner, an exact file/scope, dependencies and
   observable acceptance criteria. Parallelize only independent work. One writer
   edits a file at a time.
4. Run the assigned authorized work. Qwen runs in VS Code's visible integrated
   terminal; use the existing harness for a durable bounded job where useful.
   Use Gemini's native environment for design and research, or its existing
   bounded runner for a concrete review. Do not claim a provider ran unless it did.
5. Codex reviews complete drafts before applying, tests the integrated behavior,
   and reports the actual attribution, evidence and unfinished acceptance.

A suitable opening is: "Split: Gemini's story supplies the design; Qwen drafts
one pure-function test; Codex integrates and verifies." If a role is unavailable,
state that and continue independent work. No hidden provider/model substitution.

## Responsibilities

| Owner | Work | Output |
| --- | --- | --- |
| Magne | Product scope and external publication/access approval | Clear objective and authorization |
| Gemini | Requirements, research, architecture and independent review | One story contract or concrete findings |
| Codex | Implementation, integration, security-sensitive execution and final checks | Reviewed changes and verification evidence |
| Qwen 3.8 | One bounded source/test/doc draft | Inspectable draft, never applied automatically |

Gemini remains design/review owner. The active conversation's coordinator handles
dispatch; a request in Codex is not automatically delivered to Gemini. Keep shared
context in one story rather than copying provider transcripts. Existing harness
labels that route architecture to Codex are legacy execution constraints, not a
replacement for these responsibilities. Do not redesign that gate just to route
ordinary design dialogue.

## Local worker contract

Use `qwen3.8:27b-q4_K_M` through `scripts/local_worker.py`, with low reasoning by
default. Allowed choices are low, medium and xhigh. The helper verifies advertised
levels, or the narrowly supported Ollama 0.32.15 `qwen3.8` renderer when that
version omits thinking metadata. That renderer accepts low/medium and maps native
high to Qwen xhigh. Unknown versions/renderers and incompatible explicit metadata
stop before inference. The helper uses 8,192 context tokens, a 12 KB
combined input cap and 2,048 default output tokens. It inherits model sampling
defaults. No automatic GPT-oss fallback.

Choose a pure function, a focused unit-test draft, small UI component, explanation
or documentation excerpt. Supply relevant tracked source and exact acceptance
criteria. Keep credentials, personal training data and raw logs/transcripts out.
The curated worker memo remains the only automatic private-path exception.

For durable jobs, use `harness.py create`, run the returned ID visibly, inspect
the full draft, record `review --checks`, apply the reviewed change, run checks
and record `complete --checks`. Acceptance does not mean a draft was applied.
Allow at most one correction for Qwen and three total Gemini attempts. Stop and
reassess after those bounds. Never execute generated commands automatically.

Qwen's helper saves unique output under `.local/worker/`, with model/agent usage
recorded in `workload.html`. Keep historical GPT-oss rows. Counts do not measure
quality or hosted-token savings. Benchmark outcomes before assigning larger work.

## Review and release

Codex sends Gemini the shared story, concrete diff and check evidence for an
independent review when that review is part of the assigned task. Missing review
is reported explicitly. Preserve OAuth, private backend IAM and source provenance.

Run local release checks separately, seal the reviewed payload, then validate the
receipt. Public GitHub publication and Cloud Run deployment still require the
exact authorization described in `docs/LOCAL_DEPLOY_PROMPT.md`. Per-prompt routing
does not authorize deployment, credentials, destructive work or extra cloud access.

Keep VS Code open. Do not save unrelated dirty editor buffers, reload the window
or discard local work to make a workflow easier.

Model controls: [official Qwen tag](https://ollama.com/library/qwen3.8:27b-q4_K_M),
[Ollama thinking metadata](https://docs.ollama.com/capabilities/thinking),
[verified 0.32.15 renderer](https://github.com/ollama/ollama/blob/v0.32.15/model/renderers/qwen35.go#L104-L123).
