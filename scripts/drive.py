#!/usr/bin/env python3
"""
Google Drive uploaden met een OAuth refresh-token.

Bewust geen service account: die heeft zelf geen opslagquotum, en uploaden
naar een persoonlijke Drive loopt daar op stuk. Met een refresh-token zijn de
bestanden eigendom van de gebruiker en gaan ze van diens eigen quotum af.

Verwacht in de omgeving:
  GDRIVE_CLIENT_ID
  GDRIVE_CLIENT_SECRET
  GDRIVE_REFRESH_TOKEN

Gebruikt alleen de standaardbibliotheek, zodat de Action niets hoeft te
installeren.
"""
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
CHUNK = 8 << 20  # 8 MB


class DriveError(RuntimeError):
    pass


class Drive:
    def __init__(self):
        missing = [k for k in ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET",
                               "GDRIVE_REFRESH_TOKEN") if not os.environ.get(k)]
        if missing:
            raise DriveError("ontbrekende secrets: " + ", ".join(missing))
        self._token = None

    # ---------- auth ----------

    @property
    def token(self):
        if self._token:
            return self._token
        data = urllib.parse.urlencode({
            "client_id": os.environ["GDRIVE_CLIENT_ID"],
            "client_secret": os.environ["GDRIVE_CLIENT_SECRET"],
            "refresh_token": os.environ["GDRIVE_REFRESH_TOKEN"],
            "grant_type": "refresh_token"}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data)) as r:
                self._token = json.loads(r.read())["access_token"]
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            raise DriveError(
                f"token vernieuwen mislukt ({e.code}). Vaakste oorzaak: het "
                f"refresh-token is verlopen omdat het OAuth consent screen nog "
                f"op Testing staat. Antwoord: {body}") from None
        return self._token

    def _req(self, url, method="GET", body=None, headers=None, raw=False):
        h = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        if body is not None and not raw:
            body = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                txt = r.read()
                return json.loads(txt) if txt else {}, dict(r.headers)
        except urllib.error.HTTPError as e:
            raise DriveError(f"{method} {url.split('?')[0]} -> {e.code}: "
                             f"{e.read().decode(errors='replace')[:300]}") from None

    # ---------- mappen ----------

    def find_folder(self, name, parent):
        q = (f"name = '{name.replace(chr(39), chr(92) + chr(39))}' and "
             f"'{parent}' in parents and "
             f"mimeType = 'application/vnd.google-apps.folder' and trashed = false")
        res, _ = self._req(f"{API}/files?" + urllib.parse.urlencode(
            {"q": q, "fields": "files(id,name)", "pageSize": 10}))
        f = res.get("files") or []
        return f[0]["id"] if f else None

    def folder(self, name, parent):
        """Zoekt de map, maakt hem aan als hij nog niet bestaat."""
        got = self.find_folder(name, parent)
        if got:
            return got, False
        res, _ = self._req(f"{API}/files", "POST", {
            "name": name, "parents": [parent],
            "mimeType": "application/vnd.google-apps.folder"})
        return res["id"], True

    # ---------- uploaden ----------

    def upload(self, path, parent, name=None):
        path = Path(path)
        name = name or path.name
        size = path.stat().st_size
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"

        # resumable: betrouwbaarder dan multipart voor bestanden van 10 MB en meer
        _, head = self._req(
            f"{UPLOAD}?uploadType=resumable", "POST",
            {"name": name, "parents": [parent]},
            {"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)})
        loc = head.get("Location") or head.get("location")
        if not loc:
            raise DriveError("Drive gaf geen upload-URL terug")

        sent = 0
        with open(path, "rb") as f:
            while sent < size:
                chunk = f.read(CHUNK)
                end = sent + len(chunk) - 1
                req = urllib.request.Request(loc, data=chunk, method="PUT", headers={
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {sent}-{end}/{size}"})
                try:
                    with urllib.request.urlopen(req, timeout=600) as r:
                        body = r.read()
                        if r.status in (200, 201):
                            return json.loads(body)["id"]
                except urllib.error.HTTPError as e:
                    if e.code != 308:  # 308 = ga door met het volgende blok
                        raise DriveError(
                            f"upload van {name} mislukt ({e.code}): "
                            f"{e.read().decode(errors='replace')[:300]}") from None
                sent = end + 1
        raise DriveError(f"upload van {name} eindigde zonder bevestiging")


def available():
    return all(os.environ.get(k) for k in
               ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"))
