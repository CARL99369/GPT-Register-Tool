import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sms_tool import mailbox as mailbox_module
from sms_tool import mailbox_url_html
from sms_tool.codex_oauth import _mailbox_from_data
from sms_tool.mail_otp import _email_otp_candidate
from sms_tool.mailbox_types import MailboxAccount
from sms_tool.mailbox_url_html import parse_url_html_messages
from sms_tool.registration import _mailbox_snapshot


def _otp(mailbox, message):
    candidate = _email_otp_candidate(
        mailbox,
        message,
        keyword="verification|login code",
    )
    return candidate["otp"] if candidate else None


def test_parses_details_mail_card():
    html = """
    <article class="mail-card"><details open>
      <summary><span class="subject">Your temporary ChatGPT verification code</span>
      <span class="date">2026-08-03 16:31:32</span></summary>
      <div class="meta">From: noreply@tm.openai.com</div>
      <pre class="body">Enter this temporary verification code to continue: 522477</pre>
    </details></article>
    """
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html")

    messages = parse_url_html_messages(html, mailbox.email)

    assert _otp(mailbox, messages[0]) == "522477"
    assert messages[0]["subject"] == "Your temporary ChatGPT verification code"
    assert messages[0]["receivedDateTime"].startswith("2026-08-03T16:31:32")
    assert "noreply@tm.openai.com" in messages[0]["from"]


def test_chinese_chatgpt_verification_subject_matches_registration_keywords():
    html = """
    <article><h3 class="subject">你的 ChatGPT 临时验证码</h3>
    <p>输入此临时验证码以继续：522477</p></article>
    """
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html")
    message = parse_url_html_messages(html, mailbox.email)[0]

    candidate = _email_otp_candidate(
        mailbox,
        message,
        keyword="verification code|login code",
    )

    assert candidate["otp"] == "522477"


def test_parses_unrelated_table_markup_without_site_specific_classes():
    html = """
    <table>
      <tr><td>OpenAI</td><td>ChatGPT login code</td><td>2026/08/03 17:10:00</td></tr>
      <tr><td colspan="3">Use code 731904 to sign in to user@icloud.com</td></tr>
    </table>
    """
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html")

    messages = parse_url_html_messages(html, mailbox.email)

    assert any(_otp(mailbox, message) == "731904" for message in messages)


def test_ignores_script_style_and_keeps_stable_message_ids():
    html = """
    <script>window.order = 123456</script><style>.x{color:#654321}</style>
    <ul>
      <li><h3>Order update</h3><p>Tracking id 111111</p></li>
      <li><h3>Your temporary ChatGPT verification code</h3><p>Your code is 246810</p></li>
    </ul>
    """
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html")

    first = parse_url_html_messages(html, mailbox.email)
    second = parse_url_html_messages(html, mailbox.email)

    assert [_otp(mailbox, item) for item in first].count("246810") == 1
    assert [item["id"] for item in first] == [item["id"] for item in second]
    assert all("123456" not in item["body"]["content"] for item in first)
    assert all("654321" not in item["body"]["content"] for item in first)


def test_visible_text_fallback_builds_code_context_candidate():
    html = """
    <html><head><title>Inbox</title></head>
    <body><div>ChatGPT verification code: 864209</div></body></html>
    """
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html")

    messages = parse_url_html_messages(html, mailbox.email)

    assert any(_otp(mailbox, message) == "864209" for message in messages)


class FakeResponse:
    status_code = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}
    encoding = "utf-8"
    payload = b"<h1>ChatGPT verification code</h1><p>Your code is 135790</p>"

    def __init__(self):
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset:offset + chunk_size]

    def close(self):
        self.closed = True


def test_fetch_honors_proxy_follows_redirects_and_returns_messages():
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://secret@example.test/token/value?key=hidden",
    )

    messages = mailbox_url_html.fetch_url_html_messages(
        mailbox,
        proxy="http://127.0.0.1:7890",
        http_get=fake_get,
    )

    assert calls[0][1]["proxies"] == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }
    assert calls[0][1]["allow_redirects"] is True
    assert messages
    assert mailbox_url_html.redact_inbox_url(mailbox.inbox_url) == "https://example.test/<redacted>"


def test_fetch_rejects_non_html_and_oversized_response_without_leaking_url():
    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://example.test/private-token",
    )
    response = FakeResponse()
    response.headers = {"Content-Type": "application/octet-stream"}

    with pytest.raises(mailbox_url_html.UrlHtmlMailboxError) as error:
        mailbox_url_html.fetch_url_html_messages(
            mailbox,
            http_get=lambda *args, **kwargs: response,
        )

    assert "private-token" not in str(error.value)
    assert response.closed

    oversized = FakeResponse()
    oversized.payload = b"x" * (2 * 1024 * 1024 + 1)
    with pytest.raises(mailbox_url_html.UrlHtmlMailboxError, match="exceeds 2 MiB"):
        mailbox_url_html.fetch_url_html_messages(
            mailbox,
            http_get=lambda *args, **kwargs: oversized,
        )
    assert oversized.closed


def test_url_provider_credentials_fetch_and_snapshot(monkeypatch):
    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://example.test/inbox",
    )
    old = {
        "id": "old",
        "subject": "ChatGPT verification code",
        "bodyPreview": "code 111222",
        "body": {"content": ""},
    }
    monkeypatch.setattr(
        mailbox_url_html,
        "fetch_url_html_messages",
        lambda *args, **kwargs: [old],
    )

    assert mailbox_module.mailbox_has_inbox_credentials(mailbox)
    assert mailbox_module._fetch_mailbox_messages(mailbox) == [old]
    assert mailbox_module._snapshot_mailbox_message(mailbox) == "old"
    assert mailbox.seen_message_ids == ("old",)


def test_url_poll_ignores_baseline_and_returns_new_code(monkeypatch):
    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://example.test/inbox",
        seen_message_ids=("old",),
    )
    old = {
        "id": "old",
        "subject": "ChatGPT verification code",
        "bodyPreview": "code 111222",
        "body": {"content": ""},
    }
    new = {
        "id": "new",
        "subject": "ChatGPT verification code",
        "bodyPreview": "code 333444",
        "body": {"content": ""},
    }
    monkeypatch.setattr(
        mailbox_module,
        "_fetch_mailbox_messages",
        lambda *args, **kwargs: [new, old],
    )
    monkeypatch.setattr(
        mailbox_module,
        "_email_cfg",
        lambda: {"otp_poll_interval": 0.01, "otp_settle_seconds": 0},
    )

    code = mailbox_module._poll_email_otp(
        mailbox,
        subject_keyword="verification",
        timeout=0.2,
    )

    assert code == "333444"


def test_url_mailbox_survives_session_snapshot_round_trip():
    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://example.test/private/inbox",
    )

    restored = _mailbox_from_data({"mailbox": _mailbox_snapshot(mailbox)})

    assert restored.provider == "url_html"
    assert restored.inbox_url == mailbox.inbox_url


def test_fetches_mailbox_from_local_http_server():
    class InboxHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = (
                b"<article><h2>ChatGPT verification code</h2>"
                b"<p>Your code is 908172</p></article>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), InboxHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        mailbox = MailboxAccount(
            email="user@icloud.com",
            provider="url_html",
            inbox_url=f"http://127.0.0.1:{server.server_port}/inbox",
        )

        messages = mailbox_url_html.fetch_url_html_messages(mailbox)

        assert _otp(mailbox, messages[0]) == "908172"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
