from sms_tool.mail_otp import _email_otp_candidate
from sms_tool.mailbox_types import MailboxAccount
from sms_tool.mailbox_url_html import parse_url_html_messages


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
