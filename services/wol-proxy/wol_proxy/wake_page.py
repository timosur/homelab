"""Wake-up page HTML template served when the desktop node is sleeping."""

WAKE_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Starting up – {app_name}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #0f172a; color: #e2e8f0;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh;
    }}
    .card {{
      text-align: center; max-width: 420px; padding: 2rem;
    }}
    .spinner {{
      width: 48px; height: 48px; margin: 0 auto 1.5rem;
      border: 4px solid #334155; border-top-color: #38bdf8;
      border-radius: 50%; animation: spin 1s linear infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    h1 {{ font-size: 1.25rem; margin-bottom: 0.5rem; }}
    #status {{ color: #94a3b8; font-size: 0.9rem; }}
    #elapsed {{ color: #64748b; font-size: 0.8rem; margin-top: 1rem; }}
    .ready .spinner {{ border-top-color: #4ade80; animation: none; }}
    .ready h1 {{ color: #4ade80; }}
  </style>
</head>
<body>
  <div class="card" id="card">
    <div class="spinner" id="spinner"></div>
    <h1 id="title">Desktop-Node wird hochgefahren\u2026</h1>
    <p id="status">WoL-Paket gesendet. Bitte warten.</p>
    <p id="elapsed"></p>
  </div>
  <script>
    const start = Date.now();
    const poll = setInterval(async () => {{
      try {{
        const r = await fetch('/wol-proxy/status');
        const d = await r.json();
        const el = document.getElementById('status');
        const sec = Math.round((Date.now() - start) / 1000);
        document.getElementById('elapsed').textContent = sec + 's';
        if (d.state === 'ready') {{
          clearInterval(poll);
          document.getElementById('card').classList.add('ready');
          document.getElementById('title').textContent = 'Bereit!';
          el.textContent = 'Weiterleitung\u2026';
          setTimeout(() => window.location.reload(), 500);
        }} else if (d.state === 'waking') {{
          el.textContent = 'Node startet\u2026 (' + sec + 's)';
        }} else {{
          el.textContent = 'Warte auf Aufwach-Signal\u2026';
        }}
      }} catch (e) {{
        // ignore transient fetch failures
      }}
    }}, 3000);
  </script>
</body>
</html>
"""


def render_wake_page(app_name: str) -> str:
    return WAKE_PAGE_HTML.format(app_name=app_name)
