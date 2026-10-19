from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Credentials:
    api_url: str
    username: str
    access: str
    refresh: str
    project_id: str = ""
    project_name: str = ""


def load_credentials(path: Path) -> Credentials | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text())
    return Credentials(**raw)


def save_credentials(path: Path, creds: Credentials) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(creds), indent=2))
    tmp.replace(path)


def clear_credentials(path: Path) -> None:
    path.unlink(missing_ok=True)


class SolarianBaseClient:
    def __init__(self, api_url: str, creds: Credentials | None = None):
        self.api_url = api_url.rstrip("/")
        self.creds = creds

    def login(self, username: str, password: str) -> Credentials:
        payload = _post_json(
            f"{self.api_url}/api/auth/token/",
            {"username": username, "password": password},
        )
        self.creds = Credentials(
            api_url=self.api_url,
            username=username,
            access=str(payload["access"]),
            refresh=str(payload["refresh"]),
        )
        return self.creds

    def refresh(self) -> None:
        if self.creds is None:
            raise RuntimeError("not logged in")
        payload = _post_json(
            f"{self.api_url}/api/auth/token/refresh/",
            {"refresh": self.creds.refresh},
        )
        self.creds.access = str(payload["access"])
        if payload.get("refresh"):
            self.creds.refresh = str(payload["refresh"])

    def me(self) -> dict:
        return self._auth_json("GET", "/api/users/me/")

    def projects(self) -> list[dict]:
        data = self._auth_json("GET", "/api/projects/")
        return list(data)

    def upload_zip(
        self,
        zip_path: Path,
        *,
        project_id: str,
        name: str,
        description: str = "",
    ) -> dict:
        """Store a raw LUMI session zip (UMI-compatible MP4s).

        Solarian Base's dataset API currently accepts ``lerobot`` or ``zarr``.
        A zarr upload is stored as an opaque zip, which is how raw
        ``raw_videos/`` sessions land in the project until a dedicated
        ``umi`` format exists.
        """
        if self.creds is None:
            raise RuntimeError("not logged in")
        boundary = f"----umi{int(time.time() * 1000)}"
        file_name = zip_path.name if zip_path.name.endswith(".zarr.zip") else f"{zip_path.stem}.zarr.zip"
        fields = {
            "name": name,
            "description": description,
            "format": "zarr",
            "project_id": project_id,
        }
        body = bytearray()
        for key, value in fields.items():
            body += (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        file_bytes = zip_path.read_bytes()
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode()
        body += file_bytes
        body += f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{self.api_url}/api/datasets/upload/",
            data=bytes(body),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.creds.access}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            self.refresh()
            req.add_header("Authorization", f"Bearer {self.creds.access}")
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read().decode())

    def _auth_json(self, method: str, path: str) -> dict | list:
        if self.creds is None:
            raise RuntimeError("not logged in")

        def once(token: str):
            req = urllib.request.Request(
                f"{self.api_url}{path}",
                method=method,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())

        try:
            return once(self.creds.access)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            self.refresh()
            return once(self.creds.access)


def _post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"login failed: {exc.code} {detail}") from exc
