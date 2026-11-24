import html
from datetime import datetime, timezone


def render_codex_usage(turns: list[dict]) -> str:
    """Render measured turn snapshots separately from manually recorded stages."""
    def number(value):
        return f'{value:,}' if type(value) is int else '—'

    def total(key):
        measured = [t[key] for t in turns if type(t.get(key)) is int]
        return number(sum(measured)) if measured else '—'

    if not turns:
        return '<h2>Codex usage per prompt (turn)</h2><p>No measured Codex turns yet.</p>'

    rows = []
    for turn in reversed(turns):
        inp, cached = turn.get('prompt_tokens'), turn.get('cached_input_tokens')
        new_input = inp - cached if type(inp) is int and type(cached) is int else None
        try:
            started = datetime.fromisoformat(turn['started_at']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        except (KeyError, TypeError, ValueError):
            started = '—'
        cells = [html.escape(str(turn.get(key) or '—')) for key in ('title', 'model', 'status')]
        cells += [started, number(inp), number(cached), number(new_input),
                  number(turn.get('output_tokens')), number(turn.get('total_tokens'))]
        rows.append('<tr>' + ''.join(f'<td>{cell}</td>' for cell in cells) + '</tr>')
    return f'''
        <h2>Codex usage per prompt (turn)</h2>
        <div class="metrics">
            <div class="card"><h2>Measured Codex input</h2><p>{total('prompt_tokens')}</p></div>
            <div class="card"><h2>Of which cached</h2><p>{total('cached_input_tokens')}</p></div>
            <div class="card"><h2>Measured Codex output</h2><p>{total('output_tokens')}</p></div>
        </div>
        <p class="notes">One row covers all model calls in a Codex turn. Input includes cached input;
        new input is input minus cached input. Repeated context counts again on each call.
        Output includes reasoning where reported. Total is input plus output.
        These are local session counters, not subscription allowance, billing, or measured savings.
        Running turns are partial; a dash means unavailable. Automatic continuations may have their own turn.
        Only counters and metadata are imported: no prompt bodies. Newest turns appear first.</p>
        <div class="table-scroll"><table><thead><tr>
            <th>Prompt / session</th><th>Model</th><th>Status</th><th>Started (UTC)</th>
            <th>Input</th><th>Cached input</th><th>New input</th><th>Output</th><th>Total</th>
        </tr></thead><tbody>{''.join(rows)}</tbody></table></div>
    '''


def render_report(tasks: list[dict], codex_turns: list[dict] | None = None) -> str:
    def format_timestamp(ts):
        return datetime.fromisoformat(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else '—'

    def format_number(n):
        return html.escape(str(n)) if n is not None else '—'

    codex_done = sum(1 for t in tasks if t['agent'] == 'codex' and t['status'] == 'done')
    local_done = sum(1 for t in tasks if t['agent'] != 'codex' and t['status'] == 'done')
    measured = [t['output_tokens'] for t in tasks if t['agent'] != 'codex' and t.get('output_tokens') is not None]
    local_output_tokens = sum(measured) if measured else None

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="refresh" content="5">
        <title>Codex + Local Workers</title>
        <style>
            * {{ box-sizing: border-box; }}
            :root {{ color-scheme: light dark; }}
            .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }}
            .table-scroll {{ overflow-x: auto; }}
            .workflow {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 24px 0; }}
            .workflow span {{ padding: 10px 14px; background: var(--card-bg-color); border-radius: 6px; }}
            .notes {{ line-height: 1.6; }}
            td {{ overflow-wrap: anywhere; min-width: 90px; }}
            td:nth-child(2) {{ min-width: 180px; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
                margin: 0;
                padding: 0;
                display: flex;
                flex-direction: column;
                align-items: center;
                background-color: var(--bg-color);
                color: var(--text-color);
                transition: background-color 0.3s, color 0.3s;
            }}
            .container {{
                width: 90%;
                max-width: 1200px;
                margin: 20px 0;
            }}
            .card {{
                background-color: var(--card-bg-color);
                border-radius: 8px;
                box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
                margin: 10px 0;
                padding: 20px;
                width: 100%;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .card h2 {{
                margin: 0;
                font-size: 1.2em;
            }}
            .card p {{
                margin: 5px 0 0;
                font-size: 0.9em;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 20px;
                overflow-x: auto;
            }}
            th, td {{
                border: 1px solid var(--border-color);
                padding: 8px;
                text-align: left;
            }}
            th {{
                background-color: var(--header-bg-color);
            }}
            @media (prefers-color-scheme: dark) {{
                :root {{
                    --bg-color: #121212;
                    --text-color: #ffffff;
                    --card-bg-color: #1e1e1e;
                    --border-color: #333333;
                    --header-bg-color: #222222;
                }}
            }}
            @media (prefers-color-scheme: light) {{
                :root {{
                    --bg-color: #ffffff;
                    --text-color: #000000;
                    --card-bg-color: #f9f9f9;
                    --border-color: #dddddd;
                    --header-bg-color: #e0e0e0;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Codex + Local Workers</h1>
            <p>Report rebuilt: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
            <div class="workflow" aria-label="Delegation workflow">
                <span>Gemini · Design &amp; review</span> → <span>Codex · Split &amp; implement</span> → <span>Qwen · Bounded draft</span> → <span>Codex · Verify &amp; integrate</span>
            </div>
            <div class="metrics">
            <div class="card">
                <h2>Codex stages done</h2>
                <p>{codex_done}</p>
            </div>
            <div class="card">
                <h2>Local drafts done</h2>
                <p>{local_done}</p>
            </div>
            <div class="card">
                <h2>Known local generated tokens</h2>
                <p>{format_number(local_output_tokens)}</p>
            </div>
            </div>
            <p class="notes">Codex stages are recorded manually by the assistant; local requests through the helper are logged automatically.
            Counts show completed stages and drafts, not effort or token savings. Measured Codex turns appear below.
            Local “done” means a draft was generated, not reviewed or accepted. A dash means unmeasured.
            Model token counts use different tokenizers and can include reasoning.
            Local draft tracking begins when enabled. The Codex importer includes available AI-Coach session history.
            Times are UTC. This page reloads every five seconds; run the usage watcher to refresh its data.</p>
            {render_codex_usage(codex_turns or [])}
            <h2>Planning stages and local drafts</h2>
            <div class="table-scroll">
            <table>
                <thead>
                    <tr>
                        <th>Agent</th>
                        <th>Model</th>
                        <th>Title</th>
                        <th>Status</th>
                        <th>Recorded / updated (UTC)</th>
                        <th>Elapsed Seconds</th>
                        <th>Prompt Tokens</th>
                        <th>Output Tokens</th>
                    </tr>
                </thead>
                <tbody>
    """

    for task in tasks:
        html_content += f"""
                    <tr>
                        <td>{html.escape(task.get('agent', '—'))}</td>
                        <td>{html.escape(task.get('model') or '—')}</td>
                        <td>{html.escape(task.get('title', '—'))}</td>
                        <td>{html.escape(task.get('status', '—'))}</td>
                        <td>{format_timestamp(task.get('updated_at'))}</td>
                        <td>{format_number(task.get('elapsed_seconds'))}</td>
                        <td>{format_number(task.get('prompt_tokens'))}</td>
                        <td>{format_number(task.get('output_tokens'))}</td>
                    </tr>
        """

    html_content += """
                </tbody>
            </table>
            </div>
        </div>
    </body>
    </html>
    """

    return html_content
