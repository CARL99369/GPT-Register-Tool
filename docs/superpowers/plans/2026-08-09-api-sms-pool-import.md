# API SMS Pool Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a WPF `API 接码池` path that accepts multiline `号码---URL` input and runs the existing one-click SMS workflow with a process-scoped static phone pool.

**Architecture:** A focused C# parser validates and serializes imported pairs to a temporary JSON file. The WPF one-click handler selects either the configured provider or the imported API pool, while Python loads explicit entries through the existing static `PhoneSlot` adapter. Imported entries override configured static entries for that process and do not use persistent phone-pool state.

**Tech Stack:** C# 14 / WPF / .NET 10 / xUnit, Python 3 / argparse / unittest, existing `PhonePool` and backend runner.

---

## File Structure

- Create `SmsWorkbench/ApiSmsPoolImport.cs`: parse, normalize, validate, redact, and serialize API pool entries.
- Create `SmsWorkbench/MainWindow.ApiSmsPool.cs`: provider chooser and multiline import dialog.
- Create `tests/SmsWorkbench.Tests/ApiSmsPoolImportTests.cs`: parser and temporary JSON contract tests.
- Modify `SmsWorkbench/MainWindow.Register.cs`: route one-click SMS through the selected source and pass the temporary file.
- Modify `SmsWorkbench/MainWindow.Tasks.cs`: clean temporary input files in the backend runner's `finally` block and redact `--phone-pool-file` in displayed arguments.
- Modify `sms_tool/phone_reuse.py`: validate explicit JSON entries and build a non-persistent static pool.
- Modify `sms_tool/cli.py`: add `--phone-pool-file` and forward imported entries to both registration and one-click pool construction.
- Modify `tests/test_phone_reuse_smsbower.py`: verify explicit-pool precedence and non-persistent state.
- Modify `tests/test_cli_one_click_sms.py`: verify one-click CLI forwards imported entries.

### Task 1: C# Import Parser And File Contract

**Files:**
- Create: `SmsWorkbench/ApiSmsPoolImport.cs`
- Test: `tests/SmsWorkbench.Tests/ApiSmsPoolImportTests.cs`

- [ ] **Step 1: Write failing parser tests**

Add tests covering the exact separator, multiline input, URL preservation, duplicate removal, normalization, invalid schemes, missing fields, and JSON property names:

```csharp
[Fact]
public void ParsesDocumentedPhoneUrlFormat()
{
    const string input = "19862940168---http://sms66.vip/apisms/token";
    Assert.True(ApiSmsPoolImport.TryParse(input, out var entries, out string error), error);
    Assert.Equal("+19862940168", Assert.Single(entries).Phone);
    Assert.Equal("http://sms66.vip/apisms/token", entries[0].SmsApiUrl);
}

[Fact]
public void SplitsOnlyOnFirstTripleDashAndRemovesExactDuplicates()
{
    const string input = "19862940168---https://example.test/a---b?x=1\n19862940168---https://example.test/a---b?x=1";
    Assert.True(ApiSmsPoolImport.TryParse(input, out var entries, out string error), error);
    Assert.Equal("https://example.test/a---b?x=1", Assert.Single(entries).SmsApiUrl);
}

[Theory]
[InlineData("19862940168--https://example.test/code")]
[InlineData("19862940168---file:///code")]
[InlineData("abc---https://example.test/code")]
[InlineData("19862940168---http:///missing-host")]
public void RejectsMalformedLines(string input)
{
    Assert.False(ApiSmsPoolImport.TryParse(input, out _, out string error));
    Assert.Contains("第 1 行", error);
}
```

- [ ] **Step 2: Run the focused WPF tests and verify failure**

Run: `dotnet test tests/SmsWorkbench.Tests/SmsWorkbench.Tests.csproj --filter FullyQualifiedName~ApiSmsPoolImportTests`

Expected: compilation fails because `ApiSmsPoolImport` does not exist.

- [ ] **Step 3: Implement the parser and serializer**

Create an internal record and static helper with this contract:

```csharp
internal sealed record ApiSmsPoolEntry(
    [property: JsonPropertyName("phone")] string Phone,
    [property: JsonPropertyName("sms_api_url")] string SmsApiUrl);

internal static class ApiSmsPoolImport
{
    internal static bool TryParse(
        string text,
        out IReadOnlyList<ApiSmsPoolEntry> entries,
        out string error)
    {
        var parsed = new List<ApiSmsPoolEntry>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var errors = new List<string>();
        string[] lines = (text ?? "").Replace("\r\n", "\n").Replace('\r', '\n').Split('\n');
        for (int index = 0; index < lines.Length; index++)
        {
            string line = lines[index].Trim();
            if (line.Length == 0) continue;
            int separator = line.IndexOf("---", StringComparison.Ordinal);
            if (separator <= 0)
            {
                errors.Add($"第 {index + 1} 行缺少 --- 分隔符");
                continue;
            }
            string digits = new(line[..separator].Where(char.IsDigit).ToArray());
            string url = line[(separator + 3)..].Trim();
            if (digits.Length is < 7 or > 15)
            {
                errors.Add($"第 {index + 1} 行号码无效");
                continue;
            }
            if (!Uri.TryCreate(url, UriKind.Absolute, out Uri? uri)
                || uri.Host.Length == 0
                || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
            {
                errors.Add($"第 {index + 1} 行 URL 无效");
                continue;
            }
            var entry = new ApiSmsPoolEntry("+" + digits, url);
            if (seen.Add(entry.Phone + "\n" + entry.SmsApiUrl)) parsed.Add(entry);
        }
        entries = parsed;
        error = string.Join(Environment.NewLine, errors);
        return errors.Count == 0 && parsed.Count > 0;
    }

    internal static string WriteTemporaryFile(IReadOnlyList<ApiSmsPoolEntry> entries)
    {
        string path = Path.Combine(Path.GetTempPath(), "api_sms_pool_" + Guid.NewGuid().ToString("N") + ".json");
        File.WriteAllText(path, JsonSerializer.Serialize(entries), new UTF8Encoding(false));
        return path;
    }
}
```

- [ ] **Step 4: Run focused tests and verify pass**

Run: `dotnet test tests/SmsWorkbench.Tests/SmsWorkbench.Tests.csproj --filter FullyQualifiedName~ApiSmsPoolImportTests`

Expected: all API pool parser tests pass.

### Task 2: Python Explicit Phone Pool Loading

**Files:**
- Modify: `sms_tool/phone_reuse.py`
- Test: `tests/test_phone_reuse_smsbower.py`

- [ ] **Step 1: Write failing explicit-entry tests**

```python
def test_load_phone_pool_entries_and_override_configured_pool(self):
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "pool.json"
        path.write_text(
            '[{"phone":"+19862940168","sms_api_url":"http://sms66.vip/apisms/token"}]',
            encoding="utf-8",
        )
        entries = phone_reuse.load_phone_pool_entries(path)
        with patch.dict(phone_reuse.CFG, {
            "phone_reuse": {
                "source": "phone_pool",
                "state_file": str(Path(tmp) / "saved.json"),
                "phone_pool": [{"phone": "+10000000000", "sms_api_url": "https://old.test/code"}],
            }
        }, clear=False):
            pool = create_phone_pool(source_override="phone_pool", explicit_entries=entries)

    self.assertEqual(pool.state_file, "")
    self.assertEqual([slot.phone for slot in pool.phones], ["+19862940168"])
```

Add concrete invalid-root and invalid-entry tests:

```python
def test_load_phone_pool_entries_rejects_non_array(self):
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "pool.json"
        path.write_text('{"phone":"+19862940168"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "JSON array"):
            phone_reuse.load_phone_pool_entries(path)

def test_load_phone_pool_entries_rejects_invalid_entry_without_url_secret(self):
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "pool.json"
        path.write_text('[{"phone":"+19862940168","sms_api_url":""}]', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "entry 1 is invalid") as raised:
            phone_reuse.load_phone_pool_entries(path)
    self.assertNotIn("apisms", str(raised.exception))
```

- [ ] **Step 2: Run the focused Python tests and verify failure**

Run: `python -m pytest tests/test_phone_reuse_smsbower.py -k "phone_pool_entries or explicit_entries" -q`

Expected: failure because `load_phone_pool_entries` and `explicit_entries` do not exist.

- [ ] **Step 3: Implement validated explicit entries**

Add:

```python
def load_phone_pool_entries(path: str | Path) -> list[dict]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"unable to load phone pool file: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("phone pool file must contain a JSON array")
    entries = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or not _slot_from_static_entry(item, 1, f"import:{index}"):
            raise ValueError(f"phone pool entry {index + 1} is invalid")
        entries.append(item)
    if not entries:
        raise ValueError("phone pool file contains no entries")
    return entries
```

Extend the complete signature to:

```python
def create_phone_pool(
    max_reuse_count: int = 0,
    send_cooldown_seconds: int | None = None,
    source_override: str | None = None,
    explicit_entries: list[dict] | None = None,
) -> PhonePool:
```

When explicit entries are provided, build only `import:<index>` static slots and construct `PhonePool(phones=phones, state_file="")`; otherwise retain the current configured-provider branches and configured state file.

- [ ] **Step 4: Run focused tests and verify pass**

Run: `python -m pytest tests/test_phone_reuse_smsbower.py -k "phone_pool_entries or explicit_entries" -q`

Expected: all focused tests pass.

### Task 3: CLI Argument Wiring

**Files:**
- Modify: `sms_tool/cli.py`
- Test: `tests/test_cli_one_click_sms.py`

- [ ] **Step 1: Write a failing CLI forwarding test**

Extend the existing one-click fixture with `phone_pool_file="C:/Temp/pool.json"`, patch `load_phone_pool_entries` to return one entry, and capture `create_phone_pool` arguments:

```python
with (
    patch("sms_tool.phone_reuse.load_phone_pool_entries", return_value=[entry]) as load,
    patch("sms_tool.phone_reuse.create_phone_pool", return_value=FakePhonePool()) as create,
    patch("sms_tool.phone_reuse.print_phone_pool_status"),
    patch("sms_tool.session_refresh._load_seed_session", return_value=({"email": "user@example.com"}, "")),
    patch("sms_tool.codex_oauth.refresh_codex_oauth_session", return_value={"ok": True}),
):
    cli._one_click_sms(args)

load.assert_called_once_with("C:/Temp/pool.json")
create.assert_called_once_with(
    max_reuse_count=1,
    send_cooldown_seconds=None,
    source_override="phone_pool",
    explicit_entries=[entry],
)
```

- [ ] **Step 2: Run the focused CLI test and verify failure**

Run: `python -m pytest tests/test_cli_one_click_sms.py -q`

Expected: the explicit file is not loaded or forwarded.

- [ ] **Step 3: Add and forward `--phone-pool-file`**

Register:

```python
parser.add_argument(
    "--phone-pool-file",
    default=None,
    help="JSON phone/SMS URL entries for this process; requires --phone-source phone_pool",
)
```

Use a helper in both `_registration_phone_pool` and `_one_click_sms`:

```python
def _explicit_phone_pool_entries(args):
    path = str(getattr(args, "phone_pool_file", "") or "").strip()
    if not path:
        return None
    if getattr(args, "phone_source", None) != "phone_pool":
        print("[Error] --phone-pool-file requires --phone-source phone_pool")
        raise SystemExit(2)
    from .phone_reuse import load_phone_pool_entries
    try:
        return load_phone_pool_entries(path)
    except ValueError as exc:
        print(f"[Error] {exc}")
        raise SystemExit(2) from exc
```

Pass `explicit_entries=_explicit_phone_pool_entries(args)` to `create_phone_pool`.

- [ ] **Step 4: Run CLI and phone-pool tests**

Run: `python -m pytest tests/test_cli_one_click_sms.py tests/test_phone_reuse_smsbower.py -q`

Expected: all tests pass.

### Task 4: WPF Provider Choice, Import Dialog, And Cleanup

**Files:**
- Create: `SmsWorkbench/MainWindow.ApiSmsPool.cs`
- Modify: `SmsWorkbench/MainWindow.Register.cs`
- Modify: `SmsWorkbench/MainWindow.Tasks.cs`
- Test: `tests/SmsWorkbench.Tests/ApiSmsPoolImportTests.cs`

- [ ] **Step 1: Add a failing command-argument test**

Add a pure helper assertion:

```csharp
[Fact]
public void AddsProcessScopedPhonePoolArguments()
{
    var args = new List<string> { "--one-click-sms" };
    ApiSmsPoolImport.AddBackendArguments(args, "C:\\Temp\\pool.json");
    Assert.Equal(
        new[] { "--one-click-sms", "--phone-source", "phone_pool", "--phone-pool-file", "C:\\Temp\\pool.json" },
        args);
}
```

- [ ] **Step 2: Run focused WPF tests and verify failure**

Run: `dotnet test tests/SmsWorkbench.Tests/SmsWorkbench.Tests.csproj --filter FullyQualifiedName~ApiSmsPoolImportTests`

Expected: failure because `AddBackendArguments` does not exist.

- [ ] **Step 3: Implement provider choice and import dialog**

Add `AddBackendArguments`:

```csharp
internal static void AddBackendArguments(List<string> args, string path)
{
    args.Add("--phone-source");
    args.Add("phone_pool");
    args.Add("--phone-pool-file");
    args.Add(path);
}
```

Create `ShowOneClickSmsSourceDialog` with a ComboBox containing the configured provider label and `API 接码池（号码---URL）`. Create `ShowApiSmsPoolImportDialog` with a multiline `TextBox`, a validation message, Cancel, and Start buttons. Start parses through `ApiSmsPoolImport.TryParse` and only sets `DialogResult = true` when entries are valid.

Update `OneClickSms_Click` so it validates mailbox input first, collects the chosen entries in memory, writes the temporary pool immediately before launch, and uses:

```csharp
if (apiEntries != null)
{
    temporaryPoolFile = ApiSmsPoolImport.WriteTemporaryFile(apiEntries);
    args = new List<string> { "--one-click-sms", "--workers", "1", "--refresh-timeout", "60" };
    ApiSmsPoolImport.AddBackendArguments(args, temporaryPoolFile);
}
else
{
    args = new List<string> { "--one-click-sms", "--phone-source", configuredSource, "--workers", "1", "--refresh-timeout", "60" };
}
```

Call the runner with the task name, completed arguments, and exact cleanup list:

```csharp
RunBackend(
    "一键接码(" + rows.Count + ")",
    args,
    temporaryPoolFile is null ? null : new[] { temporaryPoolFile });
```

- [ ] **Step 4: Implement guaranteed cleanup and display redaction**

Change the runner signature to:

```csharp
private async void RunBackend(string taskName, List<string> args, IReadOnlyCollection<string>? cleanupFiles = null)
```

In its existing `finally`, delete each exact file path with exception logging, after clearing `runningBackendCancellation`. Add `--phone-pool-file` to `FormatBackendArgsForDisplay` sensitive options so the token-bearing temporary input cannot be located through ordinary task logs.

- [ ] **Step 5: Run focused and complete WPF tests**

Run: `dotnet test tests/SmsWorkbench.Tests/SmsWorkbench.Tests.csproj`

Expected: all WPF tests pass.

### Task 5: End-To-End Verification And Desktop Build

**Files:**
- Verify all modified files.
- Update generated desktop output only through the repository's established publish command.

- [ ] **Step 1: Run Python regression tests**

Run: `python -m pytest -q`

Expected: the complete Python suite passes, allowing only the repository's documented skips.

- [ ] **Step 2: Run WPF regression tests**

Run: `dotnet test tests/SmsWorkbench.Tests/SmsWorkbench.Tests.csproj`

Expected: all WPF tests pass.

- [ ] **Step 3: Run formatting and diff checks**

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 4: Publish the desktop executable**

Run: `dotnet publish SmsWorkbench/SmsWorkbench.csproj -c Release -r win-x64 --self-contained false -o dist/net10`

Expected: `dist/net10/SmsWorkbench.exe` is updated successfully.

- [ ] **Step 5: Smoke-test the CLI contract**

Create a temporary JSON pool with a non-routable example URL and run `python chatgpt_phone_reg.py --help`; verify `--phone-pool-file` is listed. Then invoke the parser helper in a short Python command and verify it returns one `+19862940168` slot without printing the complete polling URL.
