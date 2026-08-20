import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sms_tool import mailbox as mailbox_module
from sms_tool import mailbox_url_html
from sms_tool.codex_oauth import _mailbox_from_data
from sms_tool.mail_otp import _email_otp_candidate
from sms_tool.mailbox_parsers import _parse_chatai_mailbox_file, _parse_url_html_line
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


@pytest.mark.parametrize(
    ("line", "expected_email", "expected_url"),
    [
        (
            "sample.one@icloud.com----------https://mail.example.test/share/one",
            "sample.one@icloud.com",
            "https://mail.example.test/share/one",
        ),
        (
            "sample.two@icloud.com-------https://mail.example.test/share/two",
            "sample.two@icloud.com",
            "https://mail.example.test/share/two",
        ),
        (
            "sample.three@icloud.com------https://mail.example.test/share/three",
            "sample.three@icloud.com",
            "https://mail.example.test/share/three",
        ),
    ],
)
def test_url_html_mailbox_accepts_four_or_more_separator_hyphens(
    line, expected_email, expected_url
):
    mailbox = _parse_url_html_line(line, "mailboxes.txt", 1)

    assert mailbox is not None
    assert mailbox.provider == "url_html"
    assert mailbox.email == expected_email
    assert mailbox.inbox_url == expected_url


def test_mailbox_file_loader_prioritizes_url_html_over_chongzhi_separator(tmp_path):
    source = tmp_path / "mailboxes.txt"
    source.write_text(
        "user@example.com----------https://mail.example.test/share/token\n",
        encoding="utf-8",
    )

    records = _parse_chatai_mailbox_file(source)

    assert len(records) == 1
    assert records[0].provider == "url_html"
    assert records[0].inbox_url == "https://mail.example.test/share/token"
    assert mailbox_module.mailbox_has_inbox_credentials(records[0])


def test_parse_password_totp_account_line(tmp_path):
    source = tmp_path / "accounts.txt"
    source.write_text(
        "user@example.com----account-password----JBSWY3DPEHPK3PXP---\n",
        encoding="utf-8",
    )

    records = _parse_chatai_mailbox_file(source)

    assert len(records) == 1
    assert records[0].provider == "account_mfa"
    assert records[0].email == "user@example.com"
    assert records[0].password == "account-password"
    assert records[0].totp_secret == "JBSWY3DPEHPK3PXP"


def test_parse_password_totp_account_line_with_duplicate_secret(tmp_path):
    source = tmp_path / "accounts.txt"
    source.write_text(
        "user@example.com----account-password----JBSWY3DPEHPK3PXP----JBSWY3DPEHPK3PXP\n",
        encoding="utf-8",
    )

    records = _parse_chatai_mailbox_file(source)

    assert len(records) == 1
    assert records[0].provider == "account_mfa"
    assert records[0].totp_secret == "JBSWY3DPEHPK3PXP"


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


def test_parses_otp_from_iframe_srcdoc_mail_body():
    html = """
    <article class="mail">
      <div class="mail-head">
        <h2 class="subject">Your temporary ChatGPT login code</h2>
        <div class="meta">From: noreply@tm.openai.com</div>
      </div>
      <iframe class="body-frame" srcdoc="&lt;html&gt;&lt;body&gt;
        &lt;p&gt;Enter this temporary verification code to continue: 519317&lt;/p&gt;
      &lt;/body&gt;&lt;/html&gt;"></iframe>
    </article>
    """
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html")

    messages = parse_url_html_messages(html, mailbox.email)

    assert any(_otp(mailbox, message) == "519317" for message in messages)


def test_parses_otp_from_javascript_assigned_iframe_srcdoc():
    html = r'''
    <div>接收时间：2026年08月17日 11:47:46 (北京时间)</div>
    <div class="email-frame"><iframe id="emailFrame"></iframe></div>
    <script>
      var htmlContent = "<html>\\r\\n<body><p>Your temporary ChatGPT verification code: 446387</p></body></html>";
      frame.srcdoc = htmlContent;
    </script>
    '''
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html")

    messages = parse_url_html_messages(html, mailbox.email)

    assert any(_otp(mailbox, message) == "446387" for message in messages)
    assert messages[0]["receivedDateTime"].startswith("2026-08-17T11:47:46")


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


def test_fetches_arkasm_share_mailbox_through_public_api():
    share_url = "https://icloud.arkasm.cn/share/test-share-token"
    inbox_api = (
        "https://icloud.arkasm.cn/api/public/share/test-share-token/"
        "inbox?limit=25&days=7"
    )
    message_api = (
        "https://icloud.arkasm.cn/api/public/share/test-share-token/"
        "message?uid=42&folder=INBOX"
    )
    calls = []

    def json_response(payload):
        response = FakeResponse()
        response.headers = {"Content-Type": "application/json; charset=utf-8"}
        response.payload = json.dumps(payload).encode("utf-8")
        return response

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url == inbox_api:
            return json_response({
                "success": True,
                "data": {
                    "alias": "user@icloud.com",
                    "count": 1,
                    "method": "imap",
                    "messages": [{
                        "id": "42",
                        "from": "OpenAI <noreply@tm.openai.com>",
                        "to": "user@icloud.com",
                        "subject": "Your ChatGPT verification code",
                        "date": "2026-08-08T17:36:50+08:00",
                        "preview": "Enter this temporary code to continue.",
                        "folder": "INBOX",
                    }],
                },
            })
        if url == message_api:
            return json_response({
                "success": True,
                "data": {
                    "id": "42",
                    "from": "OpenAI <noreply@tm.openai.com>",
                    "to": "user@icloud.com",
                    "subject": "Your ChatGPT verification code",
                    "date": "2026-08-08T17:36:50+08:00",
                    "preview": "Enter this temporary code to continue.",
                    "body": "Enter this temporary verification code: 482731",
                    "content_type": "text/plain",
                },
            })
        response = FakeResponse()
        response.payload = b'<html><body><div id="root"></div></body></html>'
        return response

    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url=share_url,
    )

    messages = mailbox_url_html.fetch_url_html_messages(
        mailbox,
        proxy="http://127.0.0.1:7897",
        http_get=fake_get,
    )

    assert [url for url, _ in calls] == [inbox_api, message_api]
    assert all(
        kwargs["proxies"] == {
            "http": "http://127.0.0.1:7897",
            "https": "http://127.0.0.1:7897",
        }
        for _, kwargs in calls
    )
    assert messages[0]["id"] == "arkasm:42"
    assert messages[0]["toRecipients"] == [
        {"emailAddress": {"address": "user@icloud.com"}}
    ]
    assert _otp(mailbox, messages[0]) == "482731"


def test_fetches_flysms_pickup_mailbox_through_json_api():
    pickup_url = (
        "https://flysms.xyz/icloud/pickup"
        "#email=user%40icloud.com&key=tok_test_pickup_key"
    )
    messages_api = "https://flysms.xyz/icloud/api/pickup/messages?limit=25"
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        response = FakeResponse()
        response.headers = {"Content-Type": "application/json; charset=utf-8"}
        response.payload = json.dumps({
            "email": "user@icloud.com",
            "scope": "test-scope",
            "revision": "test-revision",
            "messages": [{
                "mailbox": "INBOX",
                "uid": 4295180644,
                "subject": "Your temporary ChatGPT verification code",
                "from": "ChatGPT <noreply@tm.openai.com>",
                "to": "user@icloud.com",
                "date": "2026-08-10T11:50:58.000Z",
                "preview": "Enter this temporary verification code: 413721",
                "hasAttachments": False,
            }],
            "nextCursor": None,
        }).encode("utf-8")
        return response

    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url=pickup_url,
    )

    messages = mailbox_url_html.fetch_url_html_messages(
        mailbox,
        proxy="http://127.0.0.1:7897",
        http_get=fake_get,
    )

    assert [url for url, _ in calls] == [messages_api]
    assert calls[0][1]["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer tok_test_pickup_key",
        "X-Mailbox-Email": "user@icloud.com",
    }
    assert calls[0][1]["proxies"] == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }
    assert messages[0]["id"] == "flysms:INBOX:4295180644"
    assert messages[0]["toRecipients"] == [
        {"emailAddress": {"address": "user@icloud.com"}}
    ]
    assert _otp(mailbox, messages[0]) == "413721"


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


def test_fetch_generic_json_no_code_returns_empty_mailbox():
    response = FakeResponse()
    response.headers = {"Content-Type": "application/json; charset=utf-8"}
    response.payload = json.dumps({
        "success": False,
        "code": "no_code",
        "message": "No verification code received yet",
        "retryable": True,
    }).encode("utf-8")
    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://mail.example.test/share/private-token",
    )

    messages = mailbox_url_html.fetch_url_html_messages(
        mailbox,
        http_get=lambda *args, **kwargs: response,
    )

    assert messages == []
    assert response.closed


def test_fetch_generic_json_extracts_direct_verification_code():
    response = FakeResponse()
    response.headers = {"Content-Type": "application/json; charset=utf-8"}
    response.payload = json.dumps({
        "success": True,
        "code": "482731",
        "message": "Verification code received",
        "retryable": False,
    }).encode("utf-8")
    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://mail.example.test/share/private-token",
    )

    messages = mailbox_url_html.fetch_url_html_messages(
        mailbox,
        http_get=lambda *args, **kwargs: response,
    )

    assert len(messages) == 1
    assert _otp(mailbox, messages[0]) == "482731"


def test_fetch_generic_json_preserves_subject_for_login_code_filter():
    response = FakeResponse()
    response.headers = {"Content-Type": "application/json; charset=utf-8"}
    response.payload = json.dumps({
        "success": True,
        "code": "482731",
        "email": "user@icloud.com",
        "message_id": "msg_004469",
        "received_at": "2026-08-10T05:38:53Z",
        "subject": "Your temporary ChatGPT login code",
    }).encode("utf-8")
    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://mail.example.test/share/private-token",
    )

    messages = mailbox_url_html.fetch_url_html_messages(
        mailbox,
        http_get=lambda *args, **kwargs: response,
    )
    candidate = _email_otp_candidate(
        mailbox,
        messages[0],
        keyword="login code|登录代码",
        issued_after_unix=1786340134,
    )

    assert candidate is not None
    assert candidate["otp"] == "482731"


def test_fetch_generic_json_accepts_canonicalized_plus_alias_recipient():
    response = FakeResponse()
    response.headers = {"Content-Type": "application/json; charset=utf-8"}
    response.payload = json.dumps({
        "success": True,
        "code": "482731",
        "email": "user@icloud.com",
        "message_id": "msg_004469",
        "received_at": "2026-08-10T05:38:53Z",
        "subject": "Your temporary ChatGPT login code",
    }).encode("utf-8")
    mailbox = MailboxAccount(
        email="user+kfc@icloud.com",
        provider="url_html",
        inbox_url="https://mail.example.test/share/private-token",
    )

    messages = mailbox_url_html.fetch_url_html_messages(
        mailbox,
        http_get=lambda *args, **kwargs: response,
    )
    candidate = _email_otp_candidate(
        mailbox,
        messages[0],
        keyword="login code|登录代码",
        issued_after_unix=1786340134,
    )

    assert candidate is not None
    assert candidate["otp"] == "482731"


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


def test_url_provider_retries_direct_when_mailbox_proxy_fails(monkeypatch):
    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://example.test/inbox",
    )
    expected = [{"id": "new", "subject": "ChatGPT login code"}]
    calls = []

    def fetch(_mailbox, limit=25, proxy=""):
        calls.append(proxy)
        if proxy:
            raise mailbox_url_html.UrlHtmlMailboxError("proxy TLS failed")
        return expected

    monkeypatch.setattr(mailbox_url_html, "fetch_url_html_messages", fetch)
    monkeypatch.setattr(mailbox_module, "_resolve_mailbox_proxy", lambda proxy=None: "http://proxy.test:8080")

    assert mailbox_module._fetch_mailbox_messages(mailbox) == expected
    assert calls == ["http://proxy.test:8080", ""]


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
