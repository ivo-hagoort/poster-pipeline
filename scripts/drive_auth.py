#!/usr/bin/env python3
"""
Haalt eenmalig een Google Drive refresh-token op. Draai dit op je EIGEN
machine, niet in een Action: er komt een browservenster aan te pas.

Vooraf, in de Google Cloud Console (console.cloud.google.com):
  1. Maak een project, of kies een bestaand project.
  2. APIs & Services > Library > zoek "Google Drive API" > Enable.
  3. APIs & Services > OAuth consent screen > External > vul de velden in >
     voeg jezelf toe als Test user. Publiceren hoeft niet.
  4. APIs & Services > Credentials > Create credentials >
     OAuth client ID > type "Desktop app".
  5. Noteer de Client ID en het Client secret.

Daarna:
  python3 scripts/drive_auth.py --client-id XXX --client-secret YYY

Het script opent je browser, jij geeft toestemming, en het drukt de drie
waarden af die je als GitHub Secret moet zetten. Het token zelf komt NIET in
de repo terecht.

Let op: een refresh-token van een app in "Testing" verloopt na zeven dagen.
Zet het consent screen op "In production" om dat te voorkomen. Verificatie
door Google is niet nodig zolang jij de enige gebruiker bent.
"""
import argparse
import http.server
import json
import secrets
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/drive"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    a = ap.parse_args()

    port = free_port()
    redirect = f"http://127.0.0.1:{port}"
    state = secrets.token_urlsafe(16)
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in got and got.get("state") == state
            self.wfile.write(
                ("<h2>Gelukt. Je kunt dit venster sluiten.</h2>" if ok
                 else "<h2>Mislukt. Kijk in de terminal.</h2>").encode())

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()

    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": a.client_id, "redirect_uri": redirect,
        "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state})
    print("Browser wordt geopend. Lukt dat niet, plak deze link zelf:\n")
    print(url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    srv.serve_forever if False else None
    import time
    for _ in range(300):
        if got:
            break
        time.sleep(1)
    if got.get("state") != state or "code" not in got:
        sys.exit(f"geen geldige toestemming ontvangen: {got}")

    data = urllib.parse.urlencode({
        "code": got["code"], "client_id": a.client_id,
        "client_secret": a.client_secret, "redirect_uri": redirect,
        "grant_type": "authorization_code"}).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN, data=data)) as r:
        tok = json.loads(r.read())
    rt = tok.get("refresh_token")
    if not rt:
        sys.exit(f"geen refresh_token terug. Antwoord: {json.dumps(tok)[:300]}")

    print("\n" + "=" * 64)
    print("Zet deze drie als GitHub Secret op de repo:")
    print("  Settings > Secrets and variables > Actions > New repository secret")
    print("=" * 64)
    print(f"\nGDRIVE_CLIENT_ID\n{a.client_id}\n")
    print(f"GDRIVE_CLIENT_SECRET\n{a.client_secret}\n")
    print(f"GDRIVE_REFRESH_TOKEN\n{rt}\n")
    print("=" * 64)
    print("Bewaar het refresh-token verder nergens. Het geeft volledige")
    print("toegang tot je Drive.")


if __name__ == "__main__":
    main()
