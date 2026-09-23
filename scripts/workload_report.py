import html
from datetime import datetime

def render_report(tasks: list[dict]) -> str:
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
            <div class="workflow" aria-label="Delegation workflow">
                <span>Codex · Plan &amp; divide</span> → <span>Local model · Draft &amp; iterate</span> → <span>Codex · Review &amp; verify</span>
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
            Counts show completed stages and drafts, not effort or token savings. Codex token usage is unavailable in this report.
            Local “done” means a draft was generated, not reviewed or accepted. A dash means unmeasured.
            Model token counts use different tokenizers and can include reasoning.
            Tracking begins when enabled; earlier runs are not reconstructed. Times are UTC. This page refreshes every five seconds.</p>
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
