from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _method_body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_one_click_mfa_requires_phone_source_dialog():
    source = (ROOT / "SmsWorkbench" / "MainWindow.Register.cs").read_text(encoding="utf-8-sig")
    method = _method_body(source, "private async void OneClickSms_Click", "private void OneClickScan_Click")

    assert "accountMfaOnly" not in method
    assert "if (!ShowOneClickSmsSourceDialog" in method
    assert "if (apiEntries == null)" in method


def test_desktop_labels_deactivated_accounts_as_disabled():
    source = (ROOT / "SmsWorkbench" / "MainWindow.Helpers.cs").read_text(encoding="utf-8-sig")
    method = _method_body(source, "private string DisplayAccountStatus", "private bool LooksAtInvalidError")

    assert 'return "停用";' in method
