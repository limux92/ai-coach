"""Render private job metadata. Based on a reviewed GPT-oss draft."""
import html


def render_jobs(jobs: list[dict], release=None) -> str:
    def cell(value):
        return '<td>' + html.escape(str(value if value is not None and value != '' else '—')) + '</td>'

    rows = []
    for job in sorted(jobs, key=lambda j: str(j.get('created_at') or ''), reverse=True):
        review = job.get('review') or {}
        attempts = job.get('attempts') or []
        last_attempt = attempts[-1] if attempts else {}
        values = [job.get('id'), job.get('title'), job.get('provider'), job.get('status'),
                  len(job.get('attempts') or []), review.get('verdict'),
                  (job.get('integration') or {}).get('checks') or review.get('checks'),
                  last_attempt.get('status'), last_attempt.get('error')]
        rows.append('<tr>' + ''.join(cell(value) for value in values) + '</tr>')
    body = ''.join(rows) or '<tr><td colspan="9">No jobs yet.</td></tr>'
    release = release or {'status': 'unknown', 'scope': 'No saved release attempt'}
    release_text = html.escape(' · '.join(str(release.get(key)) for key in ('status', 'stage', 'error', 'scope') if release.get(key)))
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>AI-Coach task routing</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<style>
body{{font:16px/1.5 system-ui,sans-serif;margin:0;padding:24px;background:#111827;color:#e5e7eb}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #374151;padding:10px;text-align:left}}
th{{background:#1f2937}} td{{max-width:32rem;overflow-wrap:anywhere}} .table{{overflow-x:auto}}
.notice{{border-left:4px solid #fbbf24;padding:12px;background:#1f2937}} a{{color:#93c5fd}}
</style></head><body><main>
<h1>AI-Coach task routing</h1>
<p class="notice">Latest saved release: {release_text}</p>
<p>Gemini designs and reviews; Codex coordinates and verifies; Qwen drafts bounded tasks.
Accepted means reviewed, not applied. Worker responses are not proof of deployment.
Checks are recorded evidence, not commands executed by this report.</p>
<p><a href="workload.html">Measured worker usage and Codex stages</a></p>
<div class="table"><table><thead><tr><th>Job ID</th><th>Title</th><th>Provider</th><th>Status</th>
<th>Attempts</th><th>Review</th><th>Checks</th><th>Last outcome</th><th>Last error</th></tr></thead><tbody>{body}</tbody></table></div>
<p>Only metadata appears here. Briefs and drafts stay in the ignored jobs directory.
Usage counters do not measure remaining subscription quota or token savings.</p>
</main></body></html>'''
