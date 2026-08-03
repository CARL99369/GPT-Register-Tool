from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_import_accepts_url_html_rows_without_relaxing_existing_four_part_rows():
    source = (ROOT / "SmsWorkbench" / "MainWindow.Register.cs").read_text(
        encoding="utf-8-sig"
    )

    assert 'parsed.Provider == "url_html"' in source
    assert "existingFourPart" in source
    assert "!existingFourPart && !urlHtml" in source


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

    assert 'parsed.Provider == "url_html"' in source
    assert 'AccountType = "URL邮箱池"' in source
    assert 'MailboxProvider = "url_html"' in source


def test_registered_url_html_session_rebuilds_mailbox_line():
    register_source = (ROOT / "SmsWorkbench" / "MainWindow.Register.cs").read_text(
        encoding="utf-8-sig"
    )
    pool_source = (ROOT / "SmsWorkbench" / "MainWindow.Pools.cs").read_text(
        encoding="utf-8-sig"
    )

    assert 'JsonString(mailbox, "inbox_url")' in register_source
    assert 'provider.Equals("url_html"' in register_source
    assert 'email + "----" + inboxUrl' in register_source
    assert "isUrlHtmlMailbox" in pool_source
    assert '"SQLite/URL HTML"' in pool_source
    assert '"Session/URL HTML"' in pool_source
