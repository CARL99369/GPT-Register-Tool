from pathlib import Path

from sms_tool.desktop_read import _mailbox_line


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_import_accepts_url_html_rows_without_relaxing_existing_four_part_rows():
    source = (ROOT / "SmsWorkbench" / "MainWindow.Register.cs").read_text(
        encoding="utf-8-sig"
    )

    assert "MailboxLineParser.TryParse" in source
    parser = (ROOT / "SmsWorkbench" / "MailboxLineParser.cs").read_text(encoding="utf-8-sig")
    assert 'isICloud ? "icloud_url" : "url_html"' in parser


def test_desktop_mailbox_argument_uses_shared_line_parser():
    source = (ROOT / "SmsWorkbench" / "MainWindow.Register.cs").read_text(
        encoding="utf-8-sig"
    )
    start = source.index("private string MailboxArgForLine")
    end = source.index("private string FindMailboxLineForRow", start)
    method = source[start:end]

    assert "MailboxLineParser.TryParse" in method
    assert "info.CommandArgument" in method


def test_desktop_pool_displays_url_html_mailbox_provider():
    source = (ROOT / "SmsWorkbench" / "MainWindow.Pools.cs").read_text(
        encoding="utf-8-sig"
    )

    assert '"url_html" => "URL HTML"' in source


def test_registered_url_html_session_rebuilds_mailbox_line():
    line = _mailbox_line({
        "email": "user@example.com",
        "provider": "url_html",
        "inbox_url": "https://mail.example.test/inbox/token",
    })
    assert line == "user@example.com----https://mail.example.test/inbox/token"
