# URL HTML Mailbox Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持从 `邮箱----任意HTTP(S)邮件页面URL` 导入邮箱，并从不同结构的静态 HTML 中安全、稳定地轮询 OpenAI/ChatGPT 验证码。

**Architecture:** Python 侧新增独立 `mailbox_url_html` provider，负责 HTTP 下载、URL 脱敏、HTML 语义容器/可见文本解析和标准消息生成；现有 `mailbox.py` 只负责 provider 分派、发送前基线和 OTP 轮询接线。桌面端新增无 UI 依赖的邮箱行分类器，由导入、邮箱池加载和命令参数选择共同复用。

**Tech Stack:** Python 3 标准库 `html.parser`、`hashlib`、`urllib.parse`，现有 `requests` 与 `pytest`；C# 13 / .NET 10 WPF 与 xUnit；PowerShell 正式发布脚本。

---

## 文件结构

- Create: `sms_tool/mailbox_url_html.py` — URL 下载、脱敏、HTML DOM-lite 解析和标准消息生成。
- Create: `tests/test_url_html_mailbox.py` — HTML 结构、网络边界、基线和日志脱敏测试。
- Modify: `sms_tool/mailbox_types.py` — 增加 `inbox_url` 与 URL provider 的基线消息 ID 集合。
- Modify: `sms_tool/mailbox_parsers.py` — 识别两字段 URL 邮箱导入行。
- Modify: `sms_tool/mailbox.py` — provider 分派、凭据检查、快照与轮询过滤。
- Modify: `tests/test_registration_concurrency.py` — 导入格式回归测试。
- Create: `SmsWorkbench/MailboxLineParser.cs` — 统一解析 URL/Chatai/Graph/Gmail/ReMail/CFWorker 行并返回命令参数。
- Create: `tests/SmsWorkbench.Tests/MailboxLineParserTests.cs` — 桌面端格式分类测试。
- Modify: `SmsWorkbench/MainWindow.Register.cs` — 导入和所选邮箱参数使用统一分类器。
- Modify: `SmsWorkbench/MainWindow.Pools.cs` — URL 邮箱池行展示与 provider 标记。
- Modify: `README.md` — 记录新导入格式、静态 HTML 边界和仅邮箱注册流程。
- Modify: `docs/directory-map.md` — 登记新增 provider 与桌面分类器。

### Task 1: Python 账户模型与导入格式

**Files:**
- Modify: `sms_tool/mailbox_types.py`
- Modify: `sms_tool/mailbox_parsers.py`
- Modify: `tests/test_registration_concurrency.py`

- [ ] **Step 1: 写 URL 行解析失败测试**

在 `tests/test_registration_concurrency.py` 的 Chatai parser 测试旁加入：

```python
def test_chatai_parser_accepts_url_html_mailbox_and_preserves_delimiter_in_url(self):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mailboxes.txt"
        url = "https://mail.example.test/messages/key/user%40icloud.com?marker=a----b"
        path.write_text(f"User@iCloud.com----{url}\n", encoding="utf-8")

        records = _parse_chatai_mailbox_file(path)

    self.assertEqual(len(records), 1)
    self.assertEqual(records[0].email, "user@icloud.com")
    self.assertEqual(records[0].provider, "url_html")
    self.assertEqual(records[0].inbox_url, url)

def test_chatai_parser_rejects_non_http_two_field_mailbox(self):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mailboxes.txt"
        path.write_text("user@icloud.com----file:///tmp/mail.html\n", encoding="utf-8")

        records = _parse_chatai_mailbox_file(path)

    self.assertEqual(records, [])
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `python -m pytest tests/test_registration_concurrency.py -k "url_html or non_http_two_field" -q`

Expected: FAIL，HTTP URL 行仍被现有四字段检查拒绝。

- [ ] **Step 3: 增加模型字段与明确的两字段解析器**

在 `MailboxAccount` 末尾加入：

```python
    inbox_url: str = ""
    seen_message_ids: tuple[str, ...] = ()
```

在 `mailbox_parsers.py` 增加并在四字段 Chatai 分支之前调用：

```python
def _parse_url_html_line(line, source_path, line_no):
    email_text, separator, inbox_url = str(line or "").partition("----")
    if not separator:
        return None
    email = _normalize_mailbox_email(email_text)
    inbox_url = inbox_url.strip()
    parsed_url = urlsplit(inbox_url)
    if not email or parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.hostname:
        return None
    return MailboxAccount(
        email=email.lower(),
        source=str(source_path),
        provider="url_html",
        inbox_url=inbox_url,
    )
```

同时从 `urllib.parse` 导入 `urlsplit`。分支只在 `partition("----")` 后的完整右侧是带主机名的 HTTP(S) URL 时返回账户；否则继续执行原四字段 Chatai 逻辑，确保刷新令牌中包含 `----` 的现有格式不回归。

- [ ] **Step 4: 运行 parser 回归测试**

Run: `python -m pytest tests/test_registration_concurrency.py -k "chatai_parser" -q`

Expected: PASS，新增两条及所有既有 Chatai parser 测试通过。

- [ ] **Step 5: 提交账户格式改动**

```powershell
git add sms_tool/mailbox_types.py sms_tool/mailbox_parsers.py tests/test_registration_concurrency.py
git commit -m "feat: parse URL HTML mailbox records"
```

### Task 2: 通用静态 HTML 消息解析

**Files:**
- Create: `sms_tool/mailbox_url_html.py`
- Create: `tests/test_url_html_mailbox.py`

- [ ] **Step 1: 为两种不同 HTML 结构写失败测试**

创建 `tests/test_url_html_mailbox.py`，覆盖用户示例式卡片和无类名的表格结构：

```python
from sms_tool.mailbox_types import MailboxAccount
from sms_tool.mailbox_url_html import parse_url_html_messages
from sms_tool.mail_otp import _email_otp_candidate


def _otp(mailbox, message):
    candidate = _email_otp_candidate(mailbox, message, keyword="verification|login code")
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


def test_parses_unrelated_table_markup_without_site_specific_classes():
    html = """
    <table><tr><td>OpenAI</td><td>ChatGPT login code</td><td>2026/08/03 17:10:00</td></tr>
    <tr><td colspan="3">Use code 731904 to sign in to user@icloud.com</td></tr></table>
    """
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html")
    messages = parse_url_html_messages(html, mailbox.email)
    assert any(_otp(mailbox, message) == "731904" for message in messages)
```

- [ ] **Step 2: 写噪声、顺序与稳定 ID 失败测试**

在同一文件加入：

```python
def test_ignores_script_style_and_keeps_separate_message_ids():
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


def test_visible_text_fallback_builds_code_context_candidates():
    html = "<html><head><title>Inbox</title></head><body>ChatGPT verification code: 864209</body></html>"
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html")
    messages = parse_url_html_messages(html, mailbox.email)
    assert any(_otp(mailbox, message) == "864209" for message in messages)
```

- [ ] **Step 3: 运行 HTML parser 测试并确认模块缺失**

Run: `python -m pytest tests/test_url_html_mailbox.py -q`

Expected: ERROR during collection，`sms_tool.mailbox_url_html` 尚不存在。

- [ ] **Step 4: 实现 DOM-lite 解析与标准化边界**

创建 `sms_tool/mailbox_url_html.py`。公开接口固定为：

```python
MAX_HTML_BYTES = 2 * 1024 * 1024


def parse_url_html_messages(html, mailbox_email, limit=25):
    root = _parse_html_tree(str(html or ""))
    containers = _semantic_message_containers(root)
    messages = [_message_from_node(node, mailbox_email) for node in containers]
    messages = [message for message in messages if message]
    if not messages:
        messages = _fallback_context_messages(_visible_text(root), mailbox_email)
    unique = []
    seen = set()
    for message in messages:
        if message["id"] in seen:
            continue
        seen.add(message["id"])
        unique.append(message)
        if len(unique) >= max(1, int(limit or 25)):
            break
    return unique
```

内部使用 `html.parser.HTMLParser` 构建仅包含标签、属性、文本和子节点的轻量树；`script/style/noscript/svg` 整棵忽略。容器候选顺序为最深层 `article/details/tr/li`，再匹配 class/id/role 中的 `mail/message/inbox/card/item/row`。主题依次取 `subject` 语义节点、`summary`、`h1`–`h4`、包含 ChatGPT/OpenAI 与 verification/login code 的短行。日期支持 ISO、`YYYY-MM-DD HH:MM:SS` 和 `YYYY/MM/DD HH:MM:SS`。消息 ID 使用 `sha256` 对规范化后的主题、发件人、收件人、日期与验证码附近文本做摘要，保证导航或脚本变化不会改变旧邮件 ID。

标准消息必须使用以下形状：

```python
{
    "id": "url-html:" + digest,
    "subject": subject,
    "receivedDateTime": received_iso_or_empty,
    "from": sender_text,
    "bodyPreview": visible_body[:1000],
    "body": {"content": visible_body},
    "toRecipients": (
        [{"emailAddress": {"address": recipient}}] if recipient else []
    ),
}
```

- [ ] **Step 5: 运行解析测试并修到绿灯**

Run: `python -m pytest tests/test_url_html_mailbox.py -q`

Expected: PASS，四个纯 HTML 测试全部通过。

- [ ] **Step 6: 提交通用 HTML 解析器**

```powershell
git add sms_tool/mailbox_url_html.py tests/test_url_html_mailbox.py
git commit -m "feat: extract mailbox messages from static HTML"
```

### Task 3: HTTP 下载、脱敏、快照与 OTP 轮询接线

**Files:**
- Modify: `sms_tool/mailbox_url_html.py`
- Modify: `sms_tool/mailbox.py`
- Modify: `tests/test_url_html_mailbox.py`

- [ ] **Step 1: 写 HTTP 边界和脱敏失败测试**

使用简单 fake response，不访问公网：

```python
import pytest
from sms_tool import mailbox as mailbox_module
from sms_tool.mailbox_url_html import UrlHtmlMailboxError, fetch_url_html_messages, redact_inbox_url


class FakeResponse:
    status_code = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}
    encoding = "utf-8"
    payload = b"<h1>ChatGPT verification code</h1><p>Your code is 135790</p>"

    def iter_content(self, chunk_size=65536):
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset:offset + chunk_size]

    def close(self):
        pass


def test_fetch_honors_proxy_and_returns_messages():
    calls = []
    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()
    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://secret@example.test/token/value?key=hidden",
    )
    messages = fetch_url_html_messages(mailbox, proxy="http://127.0.0.1:7890", http_get=fake_get)
    assert calls[0][1]["proxies"] == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }
    assert messages
    assert redact_inbox_url(mailbox.inbox_url) == "https://example.test/<redacted>"


def test_fetch_rejects_non_html_and_oversized_response_without_leaking_url():
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html", inbox_url="https://example.test/private-token")
    response = FakeResponse()
    response.headers = {"Content-Type": "application/octet-stream"}
    with pytest.raises(UrlHtmlMailboxError) as error:
        fetch_url_html_messages(mailbox, http_get=lambda *args, **kwargs: response)
    assert "private-token" not in str(error.value)

    oversized = FakeResponse()
    oversized.payload = b"x" * (2 * 1024 * 1024 + 1)
    with pytest.raises(UrlHtmlMailboxError, match="exceeds 2 MiB"):
        fetch_url_html_messages(mailbox, http_get=lambda *args, **kwargs: oversized)
```

- [ ] **Step 2: 写 provider 接线和旧验证码基线失败测试**

```python
def test_url_provider_credentials_fetch_and_snapshot(monkeypatch):
    mailbox = MailboxAccount(
        email="user@icloud.com",
        provider="url_html",
        inbox_url="https://example.test/inbox",
    )
    old = {"id": "old", "subject": "ChatGPT verification code", "bodyPreview": "code 111222", "body": {"content": ""}}
    monkeypatch.setattr(mailbox_module.mailbox_url_html, "fetch_url_html_messages", lambda *args, **kwargs: [old])
    assert mailbox_module.mailbox_has_inbox_credentials(mailbox)
    assert mailbox_module._snapshot_mailbox_message(mailbox) == "old"
    assert mailbox.seen_message_ids == ("old",)


def test_url_poll_ignores_baseline_and_returns_new_code(monkeypatch):
    mailbox = MailboxAccount(email="user@icloud.com", provider="url_html", inbox_url="https://example.test/inbox")
    mailbox.seen_message_ids = ("old",)
    old = {"id": "old", "subject": "ChatGPT verification code", "bodyPreview": "code 111222", "body": {"content": ""}}
    new = {"id": "new", "subject": "ChatGPT verification code", "bodyPreview": "code 333444", "body": {"content": ""}}
    monkeypatch.setattr(mailbox_module, "_fetch_mailbox_messages", lambda *args, **kwargs: [new, old])
    monkeypatch.setattr(mailbox_module, "_email_cfg", lambda: {"otp_poll_interval": 0.01, "otp_settle_seconds": 0})
    code = mailbox_module._poll_email_otp(mailbox, subject_keyword="verification", timeout=0.2)
    assert code == "333444"
```

- [ ] **Step 3: 运行接线测试并确认红灯**

Run: `python -m pytest tests/test_url_html_mailbox.py -k "fetch or provider or baseline" -q`

Expected: FAIL，HTTP API 和 `mailbox.py` provider 分派尚未实现。

- [ ] **Step 4: 实现有限下载与 URL 脱敏**

在 `mailbox_url_html.py` 增加：

```python
class UrlHtmlMailboxError(RuntimeError):
    pass


def redact_inbox_url(value):
    parsed = urlsplit(str(value or ""))
    host = parsed.hostname or "invalid-host"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme or 'https'}://{host}{port}/<redacted>"


def fetch_url_html_messages(mailbox, limit=25, proxy="", http_get=requests.get):
    url = str(getattr(mailbox, "inbox_url", "") or "").strip()
    safe_url = redact_inbox_url(url)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        response = http_get(
            url,
            headers={"Accept": "text/html,text/plain;q=0.9"},
            proxies=proxies,
            timeout=(10, 20),
            stream=True,
            allow_redirects=True,
        )
    except Exception as exc:
        raise UrlHtmlMailboxError(f"URL mailbox request failed for {safe_url}: {type(exc).__name__}") from exc
    if not 200 <= response.status_code < 300:
        response.close()
        raise UrlHtmlMailboxError(f"URL mailbox returned HTTP {response.status_code} for {safe_url}")
    content_type = str(response.headers.get("Content-Type") or "").lower()
    if not any(kind in content_type for kind in ("text/html", "text/plain", "application/xhtml+xml")):
        response.close()
        raise UrlHtmlMailboxError(f"URL mailbox returned unsupported content for {safe_url}")
    chunks = []
    size = 0
    try:
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > MAX_HTML_BYTES:
                raise UrlHtmlMailboxError(f"URL mailbox response exceeds 2 MiB for {safe_url}")
            chunks.append(chunk)
        payload = b"".join(chunks)
    except UrlHtmlMailboxError:
        raise
    except Exception as exc:
        raise UrlHtmlMailboxError(f"URL mailbox read failed for {safe_url}: {type(exc).__name__}") from exc
    finally:
        response.close()
    encoding = response.encoding or "utf-8"
    return parse_url_html_messages(payload.decode(encoding, errors="replace"), mailbox.email, limit=limit)
```

- [ ] **Step 5: 在现有 mailbox seam 接入 provider 和基线集合**

`mailbox.py` 导入 `mailbox_url_html`，并进行四处最小接线：

```python
if provider == "url_html":
    return bool(getattr(mailbox, "email", "") and getattr(mailbox, "inbox_url", ""))
```

```python
if provider == "url_html":
    return mailbox_url_html.fetch_url_html_messages(mailbox, limit=limit, proxy=proxy)
```

```python
if provider == "url_html":
    messages = _fetch_mailbox_messages(mailbox, limit=25, proxy=proxy)
    mailbox.seen_message_ids = tuple(filter(None, (_message_id(item) for item in messages)))
    mailbox.seen_message_id = mailbox.seen_message_ids[0] if mailbox.seen_message_ids else ""
    return mailbox.seen_message_id
```

在 `_latest_email_otp_candidate` 遍历中，对 URL provider 加入：

```python
seen_ids = set(getattr(mailbox, "seen_message_ids", ()) or ())
for msg in messages:
    if provider == "url_html" and _message_id(msg) in seen_ids:
        continue
```

异常日志只使用 `UrlHtmlMailboxError` 已脱敏消息，不再拼接原始 `inbox_url`。

- [ ] **Step 6: 运行 URL provider 与 OTP 回归测试**

Run: `python -m pytest tests/test_url_html_mailbox.py tests/test_email_otp_filtering.py -q`

Expected: PASS。

- [ ] **Step 7: 提交网络与 provider 接线**

```powershell
git add sms_tool/mailbox_url_html.py sms_tool/mailbox.py tests/test_url_html_mailbox.py
git commit -m "feat: poll OTP from URL HTML mailboxes"
```

### Task 4: 桌面端统一邮箱行分类与导入

**Files:**
- Create: `SmsWorkbench/MailboxLineParser.cs`
- Create: `tests/SmsWorkbench.Tests/MailboxLineParserTests.cs`
- Modify: `SmsWorkbench/MainWindow.Register.cs`
- Modify: `SmsWorkbench/MainWindow.Pools.cs`

- [ ] **Step 1: 写桌面格式分类失败测试**

创建 `MailboxLineParserTests.cs`：

```csharp
using SmsWorkbench;

namespace SmsWorkbench.Tests;

public sealed class MailboxLineParserTests
{
    [Theory]
    [InlineData("user@icloud.com----https://mail.example.test/messages/key/user%40icloud.com", "url_html", "--chatai-mailbox-file")]
    [InlineData("user@hotmail.com----pw----client----refresh", "chatai", "--chatai-mailbox-file")]
    [InlineData("user@hotmail.com---pw---refresh", "graph", "--mailbox-file")]
    [InlineData("gmail://user@gmail.com---app-password", "gmail", "--mailbox-file")]
    public void ClassifiesSupportedMailboxLines(string line, string provider, string argument)
    {
        Assert.True(MailboxLineParser.TryParse(line, out MailboxLineInfo info));
        Assert.Equal(provider, info.Provider);
        Assert.Equal(argument, info.CommandArgument);
    }

    [Theory]
    [InlineData("user@icloud.com----file:///mail.html")]
    [InlineData("user@icloud.com----javascript:alert(1)")]
    [InlineData("not-an-email----https://mail.example.test/inbox")]
    public void RejectsInvalidUrlMailboxLines(string line)
    {
        Assert.False(MailboxLineParser.TryParse(line, out _));
    }
}
```

- [ ] **Step 2: 运行 C# 测试并确认红灯**

Run: `.\.dotnet\dotnet.exe test tests\SmsWorkbench.Tests\SmsWorkbench.Tests.csproj -c Release --nologo --filter MailboxLineParserTests`

Expected: BUILD FAIL，`MailboxLineParser` 尚不存在。

- [ ] **Step 3: 实现无 UI 依赖的统一分类器**

创建 `SmsWorkbench/MailboxLineParser.cs`，公开内部结果：

```csharp
using System;
using System.Linq;

namespace SmsWorkbench
{
    internal readonly record struct MailboxLineInfo(
        string Email,
        string Provider,
        string CommandArgument,
        string NormalizedLine);

    internal static class MailboxLineParser
    {
        internal static bool TryParse(string line, out MailboxLineInfo info)
        {
            info = default;
            string value = (line ?? "").Trim().TrimStart('\ufeff');
            if (value.Length == 0 || value.StartsWith("#")) return false;

            int delimiter = value.IndexOf("----", StringComparison.Ordinal);
            if (delimiter > 0)
            {
                string email = value.Substring(0, delimiter).Trim();
                string remainder = value.Substring(delimiter + 4).Trim();
                if (LooksLikeEmail(email)
                    && Uri.TryCreate(remainder, UriKind.Absolute, out Uri? uri)
                    && uri.Host.Length > 0
                    && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps))
                {
                    info = new MailboxLineInfo(email, "url_html", "--chatai-mailbox-file", value);
                    return true;
                }
            }

            if (value.StartsWith("cfworker://", StringComparison.OrdinalIgnoreCase)
                || value.EndsWith("@edu.liziai.cloud", StringComparison.OrdinalIgnoreCase)
                || value.EndsWith("@liziai.cloud", StringComparison.OrdinalIgnoreCase))
            {
                string email = value.StartsWith("cfworker://", StringComparison.OrdinalIgnoreCase)
                    ? value.Substring("cfworker://".Length).Trim()
                    : value;
                info = new MailboxLineInfo(email, "cfworker", "--mailbox-file", value);
                return true;
            }
            if (value.StartsWith("remail://", StringComparison.OrdinalIgnoreCase))
            {
                string payload = value.Substring("remail://".Length);
                string[] parts = payload.Split(new[] { "---" }, 4, StringSplitOptions.None);
                if (parts.Length < 3) return false;
                info = new MailboxLineInfo(parts[0].Trim(), "remail", "--mailbox-file", value);
                return true;
            }
            if (value.StartsWith("gmail://", StringComparison.OrdinalIgnoreCase))
            {
                string payload = value.Substring("gmail://".Length);
                string email = payload.Split(new[] { "----", "---" }, StringSplitOptions.None)[0].Trim();
                info = new MailboxLineInfo(email, "gmail", "--mailbox-file", value);
                return true;
            }
            if (value.Contains("----")
                && value.Split(new[] { "----" }, StringSplitOptions.None).Length >= 4)
            {
                string email = value.Split(new[] { "----" }, StringSplitOptions.None)[0].Trim();
                info = new MailboxLineInfo(email, "chatai", "--chatai-mailbox-file", value);
                return true;
            }
            if (value.Contains("---")
                && value.Split(new[] { "---" }, StringSplitOptions.None).Length >= 3)
            {
                string email = value.Split(new[] { "---" }, StringSplitOptions.None)[0].Trim();
                info = new MailboxLineInfo(email, "graph", "--mailbox-file", value);
                return true;
            }
            return false;
        }

        private static bool LooksLikeEmail(string value)
        {
            int at = value.IndexOf('@');
            return at > 0 && at < value.Length - 3 && value.IndexOf('.', at) > at + 1 && !value.Any(char.IsWhiteSpace);
        }
    }
}
```

以上既有 provider 分支保持当前 `MailboxArgForLine` 的格式宽松度；只有新的 URL 两字段分支强制检查邮箱外形、协议和主机名。

- [ ] **Step 4: 让导入、参数选择和邮箱池加载共用分类器**

`MainWindow.Register.cs`：

```csharp
bool existingFourPart = line.Contains("----")
    && line.Split(new[] { "----" }, StringSplitOptions.None).Length >= 4;
bool urlHtml = MailboxLineParser.TryParse(line, out MailboxLineInfo parsed)
    && parsed.Provider == "url_html";
if (!existingFourPart && !urlHtml) { skipped++; continue; }
```

用以上条件替换现有 `parts.Length < 4` 导入限制，确保原来可导入的四段及以上记录完全保留，同时只新增 URL 两字段记录；`MailboxArgForLine` 改为：

```csharp
private string MailboxArgForLine(string line)
{
    return MailboxLineParser.TryParse(line, out MailboxLineInfo info)
        ? info.CommandArgument
        : "";
}
```

`MainWindow.Pools.cs` 在 Chatai 四字段分支之前加入 URL 分支：

```csharp
if (MailboxLineParser.TryParse(line, out MailboxLineInfo parsed) && parsed.Provider == "url_html")
{
    allRows.Add(new PoolRow
    {
        Id = "M" + (i + 1),
        CreatedAt = SafeTime(File.GetLastWriteTime(path)),
        CompletedAt = SafeTime(File.GetLastWriteTime(path)),
        Identifier = parsed.Email,
        AccountType = "URL邮箱池",
        Status = "可收信",
        RefreshToken = "URL HTML",
        Notes = path,
        SourcePath = path,
        RawLine = line,
        MailboxLine = line,
        MailboxProvider = "url_html"
    });
    continue;
}
```

选中邮箱的临时文件仍使用 `--chatai-mailbox-file`，因此 Python 会进入已扩展的统一 parser；现有 `AddRegistrationAtOnlyArgs` 保证命令继续包含 `--registration-at-only --no-phone-reuse`。

- [ ] **Step 5: 运行桌面端定向和完整测试**

Run: `.\.dotnet\dotnet.exe test tests\SmsWorkbench.Tests\SmsWorkbench.Tests.csproj -c Release --nologo --filter MailboxLineParserTests`

Expected: PASS。

Run: `.\.dotnet\dotnet.exe test GPTRegisterTool.slnx -c Release --nologo`

Expected: PASS，所有桌面端测试通过。

- [ ] **Step 6: 提交桌面端导入支持**

```powershell
git add SmsWorkbench/MailboxLineParser.cs SmsWorkbench/MainWindow.Register.cs SmsWorkbench/MainWindow.Pools.cs tests/SmsWorkbench.Tests/MailboxLineParserTests.cs
git commit -m "feat: import URL HTML mailboxes in desktop app"
```

### Task 5: 文档、全量验证与正式桌面编译

**Files:**
- Modify: `README.md`
- Modify: `docs/directory-map.md`
- Modify: `tests/test_url_html_mailbox.py`

- [ ] **Step 1: 更新操作说明**

在 README 邮箱导入部分加入以下明确说明：

```markdown
### URL HTML 邮箱

每行格式：

`邮箱地址----https://邮件网站/该邮箱的收件页面`

桌面端选择“导入邮箱”后可与其他邮箱池记录一起勾选注册。URL 页面必须在普通 HTTP 请求中直接返回邮件 HTML；当前不会执行页面 JavaScript、自动登录或爬取页面内链接。URL 可以来自任意人工确认的网站。选择该邮箱注册时会自动使用仅邮箱注册，不需要手机号或接码平台。
```

在 `docs/directory-map.md` 登记：

```markdown
| `sms_tool/mailbox_url_html.py` | URL HTML mailbox adapter | Downloads one imported HTTP(S) inbox page and normalizes static HTML into OTP message candidates. |
| `SmsWorkbench/MailboxLineParser.cs` | Desktop mailbox-line classifier | Shares URL/Chatai/Graph/provider classification across import, pool display, and selected registration. |
```

- [ ] **Step 2: 运行 Python 全量测试**

Run: `python -m pytest -q`

Expected: PASS，既有跳过项数量不增加；不访问公网。

- [ ] **Step 3: 运行桌面端全量测试**

Run: `.\.dotnet\dotnet.exe test GPTRegisterTool.slnx -c Release --nologo`

Expected: PASS。

- [ ] **Step 4: 用本地 HTTP 服务做集成冒烟**

在测试中复用 `http.server.ThreadingHTTPServer` 启动临时本地 HTML 页面，通过 `fetch_url_html_messages` 请求 `http://127.0.0.1:<随机端口>/inbox`，断言返回预期 OTP 后关闭服务。该测试证明真实 HTTP 路径可用，同时不依赖用户的敏感 URL 或公网。

Run: `python -m pytest tests/test_url_html_mailbox.py -k local_http -q`

Expected: PASS。

- [ ] **Step 5: 使用唯一受支持脚本正式发布桌面端**

Run: `powershell -ExecutionPolicy Bypass -File .\SmsWorkbench\build_dotnet.ps1`

Expected: exit code 0，并生成 `dist/net10/SmsWorkbench.exe`；不直接运行 `dotnet build`。

- [ ] **Step 6: 启动产物并确认进程可运行**

Run: `$process = Start-Process -FilePath (Resolve-Path '.\dist\net10\SmsWorkbench.exe') -WindowStyle Hidden -PassThru; Start-Sleep -Seconds 3; if ($process.HasExited) { throw "SmsWorkbench exited with code $($process.ExitCode)" }; Stop-Process -Id $process.Id`

Expected: 进程在 3 秒后仍存活，再由验证命令正常停止。

- [ ] **Step 7: 检查工作区与提交最终文档**

Run: `git diff --check; git status --short`

Expected: 只显示 README、directory map 和本计划的预期变更，不包含 `config.json`、用户 URL 或测试生成文件。

```powershell
git add README.md docs/directory-map.md tests/test_url_html_mailbox.py docs/superpowers/plans/2026-08-03-url-html-mailbox-import.md
git commit -m "docs: explain URL HTML mailbox workflow"
```

最终再次运行 `git status --short`，Expected: 空输出。
