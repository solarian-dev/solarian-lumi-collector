"""Local setup page. Typing a password on a 240x240 joystick is not viable."""

from __future__ import annotations

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from lumi_collector.auth import PrimaryKeyClient, save_credentials
from lumi_collector.config import CollectorConfig

_PAGE = """<!doctype html>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lumi collector</title>
<style>
 body { font: 16px/1.4 system-ui; max-width: 28rem; margin: 2rem auto; padding: 0 1rem; }
 input, select, button { width: 100%; margin: .3rem 0 1rem; padding: .5rem; }
 .ok { color: #1a7f37 } .err { color: #b42318 }
</style>
<h1>Lumi collector</h1>
<p class="{cls}">{message}</p>
<form method="post">
 <label>PrimaryKey URL</label>
 <input name="api_url" value="{api_url}">
 <label>Username</label>
 <input name="username" value="{username}" autocomplete="username">
 <label>Password</label>
 <input name="password" type="password" autocomplete="current-password">
 <label>Project id (optional, pick after login)</label>
 <input name="project_id" value="{project_id}">
 <button>Sign in</button>
</form>
<p>{projects}</p>
"""


class _Handler(BaseHTTPRequestHandler):
    cfg: CollectorConfig
    store: Path

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        self._page("Sign in from this phone. The collector keeps the JWT on the SD card.")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
        api_url = (form.get("api_url") or [self.cfg.primarykey_url])[0].strip()
        username = (form.get("username") or [""])[0].strip()
        password = (form.get("password") or [""])[0]
        project_id = (form.get("project_id") or [""])[0].strip()
        try:
            client = PrimaryKeyClient(api_url)
            creds = client.login(username, password)
            projects = client.projects()
            if project_id:
                match = next((p for p in projects if str(p.get("id")) == project_id), None)
                if match is None:
                    raise RuntimeError("project not found")
                creds.project_id = str(match["id"])
                creds.project_name = str(match.get("name") or "")
            elif projects:
                creds.project_id = str(projects[0]["id"])
                creds.project_name = str(projects[0].get("name") or "")
            save_credentials(self.store, creds)
            listing = "<br>".join(
                f'{p.get("name")} — <code>{p.get("id")}</code>' for p in projects
            ) or "no projects"
            self._page(f"Signed in as {username}.", cls="ok", projects=listing, creds=creds)
        except Exception as exc:
            self._page(str(exc), cls="err")

    def _page(self, message: str, cls: str = "", projects: str = "", creds=None) -> None:
        html = _PAGE.format(
            message=message,
            cls=cls,
            api_url=(creds.api_url if creds else self.cfg.primarykey_url),
            username=(creds.username if creds else ""),
            project_id=(creds.project_id if creds else ""),
            projects=projects,
        )
        data = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_setup_server(cfg: CollectorConfig) -> ThreadingHTTPServer:
    handler = partial(_Handler)
    handler.cfg = cfg
    handler.store = cfg.credentials_path
    server = ThreadingHTTPServer(("0.0.0.0", cfg.setup_port), handler)
    server.daemon_threads = True
    return server
