"""Microsoft Graph: find the underwriter's submission e-mail and prepare the reply with the report attached.

Replaces Find_Emails.bas (Outlook Sent Items scan) and ReplyFoundEmail.bas (Outlook reply + .Display). Like the macro,
the app never sends: it creates a DRAFT in the shared mailbox for a person to review and send.
Token sources: MSGRAPH_TOKEN (env) -> Posit Connect's Microsoft integration -> device-code sign-in (MS_TENANT_ID + MS_CLIENT_ID).
Scopes: Mail.Read.Shared (find), Mail.ReadWrite.Shared (draft). Unit-tested with a fake HTTP layer; not yet run against a tenant.
"""
from __future__ import annotations

import base64
import os
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import requests

from .rules import mailmatch

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ("https://graph.microsoft.com/Mail.Read.Shared https://graph.microsoft.com/Mail.ReadWrite.Shared "
          "https://graph.microsoft.com/User.Read offline_access")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class GraphError(RuntimeError):
    pass


def connect_token(session_token: str) -> tuple:
    """-> (token, error). The viewer's own Microsoft token from Posit Connect's OAuth integration."""
    if not session_token:
        return "", "not_on_connect"
    try:
        from posit import connect as pc
        creds = pc.Client().oauth.get_credentials(session_token)
        tok = (creds or {}).get("access_token", "")
        return (tok, "") if tok else ("", "Posit Connect returned no access token.")
    except ImportError:
        pass
    except Exception as exc:                                 # noqa: BLE001
        return "", f"Posit Connect could not give a Microsoft token ({type(exc).__name__}: {str(exc)[:160]})."
    server, key = os.environ.get("CONNECT_SERVER", "").rstrip("/"), os.environ.get("CONNECT_API_KEY", "")
    if not (server and key):
        return "", "Add posit-sdk to requirements.txt (or provide CONNECT_SERVER / CONNECT_API_KEY)."
    try:
        r = requests.post(f"{server}/__api__/v1/oauth/integrations/credentials", headers={"Authorization": f"Key {key}"},
                          data={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": session_token,
                                "subject_token_type": "urn:posit:connect:user-session-token"}, timeout=20)
        if r.status_code == 200 and r.json().get("access_token"):
            return r.json()["access_token"], ""
        return "", f"Posit Connect refused the token request (HTTP {r.status_code})."
    except Exception as exc:                                 # noqa: BLE001
        return "", f"Could not reach Posit Connect ({type(exc).__name__})."


def device_start(tenant: str, client: str) -> tuple:
    try:
        r = requests.post(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode",
                          data={"client_id": client, "scope": SCOPES}, timeout=20)
        d = r.json()
        if r.status_code == 200 and d.get("device_code"):
            return d, ""
        return None, (d.get("error_description") or f"HTTP {r.status_code}").split("\r")[0][:220]
    except Exception as exc:                                 # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def device_poll(tenant: str, client: str, dev: dict) -> tuple:
    try:
        r = requests.post(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", timeout=20,
                          data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": client,
                                "device_code": dev["device_code"]})
        d = r.json()
    except Exception as exc:                                 # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    if d.get("access_token"):
        d["expires_at"] = time.time() + int(d.get("expires_in", 3600)) - 60
        return d, "ok"
    if d.get("error") == "authorization_pending":
        return None, "pending"
    return None, (d.get("error_description") or d.get("error") or "sign-in failed").split("\r")[0][:220]


def _h(token: str, **extra) -> dict:
    return {"Authorization": f"Bearer {token}", **extra}


def _err(r) -> str:
    try:
        e = (r.json() or {}).get("error", {})
        txt = f"{e.get('code', '')}: {e.get('message', '')}".strip(": ")
    except Exception:                                        # noqa: BLE001
        txt = (getattr(r, "text", "") or "")[:200]
    if r.status_code in (401, 403):
        return (f"Microsoft refused access (HTTP {r.status_code}). The sign-in needs Mail.Read.Shared / Mail.ReadWrite.Shared "
                f"and permission on the shared mailbox. {txt}")
    return f"HTTP {r.status_code} {txt}"


def search_sent_items(token: str, mailbox: str, days_back: int = 365, max_items: int = 1000, http=requests) -> list:
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{GRAPH}/users/{mailbox}/mailFolders/sentitems/messages?$select=id,subject,body,sentDateTime,toRecipients"
           f"&$filter=sentDateTime ge {since}&$orderby=sentDateTime desc&$top=100")
    out = []
    while url and len(out) < max_items:
        r = http.get(url, headers=_h(token, Prefer='outlook.body-content-type="text"'), timeout=60)
        if r.status_code != 200:
            raise GraphError(_err(r))
        d = r.json()
        for m in d.get("value", []):
            out.append({"id": m.get("id", ""), "subject": m.get("subject") or "", "body": (m.get("body") or {}).get("content") or "",
                        "sent": m.get("sentDateTime", ""),
                        "to": [(x.get("emailAddress") or {}).get("address", "") for x in m.get("toRecipients", [])]})
        url = d.get("@odata.nextLink")
    return out[:max_items]


def find_submission(token: str, mailbox: str, exposure_name: str, http=requests) -> dict | None:
    return mailmatch.find_best(exposure_name, search_sent_items(token, mailbox, http=http))


def create_reply_draft(token: str, mailbox: str, message: dict, exposure_name: str, underwriter: str, attachments: list, http=requests) -> dict:
    """Create (do not send) a reply on the found message with the report attached. -> {id, webLink}."""
    r = http.post(f"{GRAPH}/users/{mailbox}/messages/{message['id']}/createReply", headers=_h(token), timeout=60)
    if r.status_code not in (200, 201):
        raise GraphError(_err(r))
    draft = r.json()
    did = draft["id"]
    quoted = (draft.get("body") or {}).get("content") or ""
    html = f"<p>Hi {mailmatch.greeting_name(underwriter)},</p><p>Please find the results for {exposure_name} attached.</p><p>Kind Regards,</p><br>{quoted}"
    patch = {"body": {"contentType": "HTML", "content": html}}
    if message.get("to"):
        patch["toRecipients"] = [{"emailAddress": {"address": a}} for a in message["to"] if a]
    rp = http.patch(f"{GRAPH}/users/{mailbox}/messages/{did}", headers=_h(token, **{"Content-Type": "application/json"}), json=patch, timeout=60)
    if rp.status_code not in (200, 201):
        raise GraphError(_err(rp))
    for name, data, mime in attachments:
        if len(data) > 2_900_000:
            raise GraphError(f"{name} is {len(data) / 1e6:.1f} MB - attach it manually (the draft was created).")
        ra = http.post(f"{GRAPH}/users/{mailbox}/messages/{did}/attachments", headers=_h(token), timeout=120,
                       json={"@odata.type": "#microsoft.graph.fileAttachment", "name": name, "contentType": mime,
                             "contentBytes": base64.b64encode(data).decode("ascii")})
        if ra.status_code not in (200, 201):
            raise GraphError(f"Could not attach {name}: {_err(ra)}")
    return {"id": did, "webLink": draft.get("webLink", "")}


def build_eml(to: list, subject: str, body: str, attachments: list, sender: str = "") -> bytes:
    """Offline fallback: an .eml draft the user can open in Outlook."""
    m = EmailMessage()
    m["Subject"] = subject
    if to:
        m["To"] = ", ".join(to)
    if sender:
        m["From"] = sender
    m["X-Unsent"] = "1"
    m.set_content(body)
    for name, data, mime in attachments:
        main, _, sub = (mime or "application/octet-stream").partition("/")
        m.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=name)
    return bytes(m)


def reply_body(exposure_name: str, underwriter: str) -> str:
    return f"Hi {mailmatch.greeting_name(underwriter)},\n\nPlease find the results for {exposure_name} attached.\n\nKind Regards,\n"
