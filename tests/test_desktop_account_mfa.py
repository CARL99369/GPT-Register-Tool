from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _method_body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_one_click_mfa_requires_phone_source_dialog():
    source = (ROOT / "SmsWorkbench" / "MainWindow.Register.cs").read_text(encoding="utf-8-sig")
    method = _method_body(source, "private async Task OneClickSmsAsync", "private string GetConfiguredPhoneSource")

    assert "accountMfaOnly" not in method
    assert "if (!ShowOneClickSmsSourceDialog" in method
    assert "if (useApiPool)" in method
    assert "phoneSource: phoneSource" in method


def test_desktop_labels_deactivated_accounts_as_disabled():
    source = (ROOT / "SmsWorkbench" / "AccountStatusInterpreter.cs").read_text(encoding="utf-8-sig")
    method = _method_body(source, "public static string DisplayAccountStatus", "public static bool LooksAtInvalidError")

    assert 'return "停用";' in method
