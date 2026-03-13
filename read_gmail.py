"""
Gmail Research Digest
Fetches emails from label:research-notes (last 24h), summarises them via Claude,
and emails the digest back to the user.
"""

import base64
import os
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import html2text
import anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Constants ────────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
GMAIL_QUERY = "label:research-notes newer_than:1d"
MAX_CHARS_PER_EMAIL = 6000
MODEL = "claude-opus-4-6"

PROMPT_TEMPLATE = """\
You are a financial professional's research assistant specialising in AI and semiconductors.

Below are {n} research pieces received in the last 24 hours (label: research-notes). \
The reader has a strong finance background but no technical background in AI or semiconductors.

For each research piece, produce the following structure:

---
**[Subject / Company / Topic]**
*From: [sender] | [date]*

**Context**
2-3 sentences: what is this company, technology, or concept? Why does it matter in the \
AI/semiconductor ecosystem? Use financial analogies where helpful \
(e.g. "TSMC is like the only licensed factory that makes the chips everyone in AI depends on \
— think toll booth on the road to AI").

**What they're saying**
A comprehensive breakdown of the key findings, data points, and arguments. Do not skip or \
over-compress — the reader wants to understand the full research. On first use of every \
technical term, add a plain-English definition in parentheses \
(e.g. "HBM (High Bandwidth Memory — the fast short-term working memory chips stacked on top \
of a GPU, like RAM but purpose-built for AI workloads)").

**Why it matters**
2-3 sentences: investment and market implications. Who wins, who loses, what signals to watch.

---

Be thorough. Prioritise clarity over brevity.

RESEARCH PIECES:
{raw_dump}"""


# ── Auth ─────────────────────────────────────────────────────────────────────

def build_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


# ── Gmail helpers ─────────────────────────────────────────────────────────────

def list_message_ids(service, query):
    result = service.users().messages().list(userId="me", q=query).execute()
    return [m["id"] for m in result.get("messages", [])]


def get_message(service, msg_id):
    return service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()


def extract_body(payload):
    """Recursively extract the best text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        h = html2text.HTML2Text()
        h.ignore_links = True
        h.ignore_images = True
        return h.handle(html)

    # multipart: prefer plain, fall back to html
    parts = payload.get("parts", [])
    plain = next((p for p in parts if p.get("mimeType") == "text/plain"), None)
    if plain:
        return extract_body(plain)
    html_part = next((p for p in parts if p.get("mimeType") == "text/html"), None)
    if html_part:
        return extract_body(html_part)
    # recurse into nested multipart
    for part in parts:
        result = extract_body(part)
        if result.strip():
            return result
    return ""


def header_value(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def build_raw_dump(service, msg_ids):
    entries = []
    for msg_id in msg_ids:
        msg = get_message(service, msg_id)
        headers = msg["payload"].get("headers", [])
        subject = header_value(headers, "Subject") or "(no subject)"
        sender = header_value(headers, "From") or "unknown"
        date = header_value(headers, "Date") or "unknown"
        body = extract_body(msg["payload"]).strip()
        if len(body) > MAX_CHARS_PER_EMAIL:
            body = body[:MAX_CHARS_PER_EMAIL] + "\n\n[... truncated ...]"
        entries.append(f"### {subject}\nFrom: {sender}\nDate: {date}\n\n{body}")
    return "\n\n---\n\n".join(entries)


def mark_as_read(service, msg_ids):
    for msg_id in msg_ids:
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()


# ── Claude ────────────────────────────────────────────────────────────────────

def summarise(raw_dump, n):
    client = anthropic.Anthropic()
    prompt = PROMPT_TEMPLATE.format(n=n, raw_dump=raw_dump)
    message = client.messages.create(
        model=MODEL,
        max_tokens=8096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ── Send email ────────────────────────────────────────────────────────────────

def send_digest(service, to_addr, digest_text, n):
    subject = f"Research Digest — {n} piece{'s' if n != 1 else ''}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = to_addr
    msg["To"] = to_addr

    # Plain text version
    msg.attach(MIMEText(digest_text, "plain", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    print(f"Digest sent to {to_addr} ({n} piece{'s' if n != 1 else ''})")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    user_email = os.environ["GMAIL_USER_EMAIL"]

    print("Authenticating with Gmail...")
    service = build_gmail_service()

    print(f"Searching: {GMAIL_QUERY}")
    msg_ids = list_message_ids(service, GMAIL_QUERY)

    if not msg_ids:
        print("No new research emails found. Exiting.")
        sys.exit(0)

    print(f"Found {len(msg_ids)} email(s). Fetching content...")
    raw_dump = build_raw_dump(service, msg_ids)

    print("Sending to Claude for summarisation...")
    digest = summarise(raw_dump, len(msg_ids))

    print("Emailing digest...")
    send_digest(service, user_email, digest, len(msg_ids))

    print("Marking source emails as read...")
    mark_as_read(service, msg_ids)

    print("Done.")


if __name__ == "__main__":
    main()
