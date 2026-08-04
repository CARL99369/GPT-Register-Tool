# WPF Standalone WebView2 Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a “直绑支付” sidebar entry that starts the existing local 5601 service when needed and embeds it in the SmsWorkbench main window with WebView2.

**Architecture:** A focused `StandaloneServiceController` owns health probing and one-time background launch. `MainWindow.Standalone.cs` owns only workspace switching and WebView2 UI state; the existing standalone Python service remains unchanged and is reached through its HTTP contract.

**Tech Stack:** WPF on .NET 10, Microsoft.Web.WebView2 1.0.4078.44, xUnit, existing Python HTTP service.

---

## File Map

- Modify `Directory.Packages.props`: centrally pin WebView2.
- Modify `SmsWorkbench/SmsWorkbench.csproj`: reference WebView2.
- Modify `SmsWorkbench/ApplicationPaths.cs`: expose the standalone launcher and WebView2 data paths.
- Create `SmsWorkbench/StandaloneServiceController.cs`: health check, single-flight startup and background process launch.
- Create `SmsWorkbench/MainWindow.Standalone.cs`: workspace navigation and WebView2 state handling.
- Modify `SmsWorkbench/MainWindow.xaml`: sidebar entry and embedded workspace.
- Modify `SmsWorkbench/MainWindow.xaml.cs`: inject the controller and external launcher.
- Modify `SmsWorkbench/AppHost.cs`: register the new controller and process launcher.
- Modify `SmsWorkbench/FileLauncher.cs`: support opening a validated HTTP URI externally.
- Create `tests/SmsWorkbench.Tests/StandaloneServiceControllerTests.cs`: controller contract tests.
- Modify `tests/SmsWorkbench.Tests/ApplicationPathsTests.cs`: new path assertions.
- Modify `tests/SmsWorkbench.Tests/DesktopWindowSmokeTests.cs`: navigation surface assertions.
- Modify `tests/SmsWorkbench.Tests/TestDoubles.cs`: standalone and URI launcher stubs.

### Task 1: Paths And WebView2 Dependency

**Files:**
- Modify: `tests/SmsWorkbench.Tests/ApplicationPathsTests.cs`
- Modify: `SmsWorkbench/ApplicationPaths.cs`
- Modify: `Directory.Packages.props`
- Modify: `SmsWorkbench/SmsWorkbench.csproj`

- [ ] **Step 1: Write the failing path assertions**

Extend `FindsRepositoryRootFromPublishedDirectory` with:

```csharp
Assert.Equal(Path.Combine(root, "run_direct.vbs"), paths.StandaloneLauncherPath);
Assert.Equal(Path.Combine(root, "runtime", "webview2"), paths.StandaloneWebViewDataDirectory);
```

- [ ] **Step 2: Run the path test and verify RED**

Run:

```powershell
& .\.dotnet\dotnet.exe test tests\SmsWorkbench.Tests\SmsWorkbench.Tests.csproj -c Release --filter ApplicationPathsTests --nologo
```

Expected: compile failure because `IApplicationPaths` and `ApplicationPaths` do not define the two properties.

- [ ] **Step 3: Add the minimal paths**

Add to `IApplicationPaths` and initialize in `ApplicationPaths`:

```csharp
public string StandaloneLauncherPath { get; }
public string StandaloneWebViewDataDirectory { get; }

StandaloneLauncherPath = Path.Combine(RootDirectory, "run_direct.vbs");
StandaloneWebViewDataDirectory = Path.Combine(RootDirectory, "runtime", "webview2");
```

Update `TestApplicationPaths` with the same derived properties.

- [ ] **Step 4: Add the centrally managed WebView2 package**

Add:

```xml
<PackageVersion Include="Microsoft.Web.WebView2" Version="1.0.4078.44" />
```

to `Directory.Packages.props`, and:

```xml
<PackageReference Include="Microsoft.Web.WebView2" />
```

to `SmsWorkbench/SmsWorkbench.csproj`.

- [ ] **Step 5: Run the path test and verify GREEN**

Run the command from Step 2. Expected: `ApplicationPathsTests` passes and package restore succeeds.

- [ ] **Step 6: Commit**

```powershell
git add Directory.Packages.props SmsWorkbench/SmsWorkbench.csproj SmsWorkbench/ApplicationPaths.cs tests/SmsWorkbench.Tests/ApplicationPathsTests.cs tests/SmsWorkbench.Tests/TestDoubles.cs
git commit -m "build: add WebView2 desktop dependency"
```

### Task 2: Standalone Service Controller

**Files:**
- Create: `tests/SmsWorkbench.Tests/StandaloneServiceControllerTests.cs`
- Create: `SmsWorkbench/StandaloneServiceController.cs`
- Modify: `SmsWorkbench/AppHost.cs`

- [ ] **Step 1: Write failing controller tests**

Create tests around this wished-for API:

```csharp
var launcher = new RecordingStandaloneProcessLauncher();
var handler = new SequenceHttpHandler(
    HttpStatusCode.ServiceUnavailable,
    HttpStatusCode.ServiceUnavailable,
    HttpStatusCode.OK);
var controller = new StandaloneServiceController(
    paths,
    launcher,
    new HttpClient(handler),
    pollInterval: TimeSpan.Zero,
    maxProbeAttempts: 3);

StandaloneServiceResult result = await controller.EnsureReadyAsync();

Assert.True(result.IsReady);
Assert.Equal(1, launcher.StartCount);
Assert.Equal(new Uri("http://127.0.0.1:5601/"), controller.ServiceUri);
```

Add separate tests proving an initially healthy service does not launch a process, a missing launcher returns a clear failure, and two concurrent calls share one startup task.

- [ ] **Step 2: Run the controller tests and verify RED**

Run:

```powershell
& .\.dotnet\dotnet.exe test tests\SmsWorkbench.Tests\SmsWorkbench.Tests.csproj -c Release --filter StandaloneServiceControllerTests --nologo
```

Expected: compile failure because the controller contracts do not exist.

- [ ] **Step 3: Implement the controller contracts**

Create the following public surface in `StandaloneServiceController.cs`:

```csharp
public sealed record StandaloneServiceResult(bool IsReady, string ErrorMessage)
{
    public static StandaloneServiceResult Ready() => new(true, "");
    public static StandaloneServiceResult Failed(string message) => new(false, message);
}

public interface IStandaloneProcessLauncher
{
    void Start(string launcherPath, string workingDirectory);
}

public interface IStandaloneServiceController
{
    Uri ServiceUri { get; }
    Task<StandaloneServiceResult> EnsureReadyAsync(CancellationToken cancellationToken = default);
}
```

`StandaloneProcessLauncher.Start` must validate the file and start `wscript.exe` with `UseShellExecute=false`, `CreateNoWindow=true`, and the VBS path in `ArgumentList`.

`StandaloneServiceController` must:

1. Probe `GET /health`.
2. Return ready without launching on any 2xx response.
3. Return `未找到直绑服务启动文件：<path>` if the service is down and the launcher is missing.
4. Launch once, then poll up to `maxProbeAttempts` using `pollInterval`.
5. Return `直绑服务启动超时，请重试。` after exhausting attempts.
6. Guard `_startupTask` with a lock and clear it after completion so concurrent calls share a startup but later retries remain possible.

- [ ] **Step 4: Register production services**

In `AppHost.Build`, add singleton registrations for `IStandaloneProcessLauncher` and `IStandaloneServiceController`. Construct the controller with a dedicated `HttpClient` whose timeout is five seconds, a 500 ms poll interval and 240 attempts, allowing up to two minutes for first-run environment setup.

- [ ] **Step 5: Run controller tests and verify GREEN**

Run the command from Step 2. Expected: all controller tests pass.

- [ ] **Step 6: Commit**

```powershell
git add SmsWorkbench/StandaloneServiceController.cs SmsWorkbench/AppHost.cs tests/SmsWorkbench.Tests/StandaloneServiceControllerTests.cs
git commit -m "feat: manage standalone payment service"
```

### Task 3: Embedded WPF Workspace

**Files:**
- Modify: `tests/SmsWorkbench.Tests/DesktopWindowSmokeTests.cs`
- Modify: `tests/SmsWorkbench.Tests/TestDoubles.cs`
- Modify: `SmsWorkbench/FileLauncher.cs`
- Modify: `SmsWorkbench/MainWindow.xaml`
- Modify: `SmsWorkbench/MainWindow.xaml.cs`
- Create: `SmsWorkbench/MainWindow.Standalone.cs`

- [ ] **Step 1: Write the failing WPF navigation assertions**

Update the MainWindow test constructor to inject `StubStandaloneServiceController` and `StubFileLauncher`, then assert:

```csharp
var standaloneButton = Assert.IsType<Button>(main.FindName("StandalonePaymentButton"));
Assert.Equal("直绑支付", AutomationProperties.GetName(standaloneButton));
var accountWorkspace = Assert.IsType<Grid>(main.FindName("AccountWorkspace"));
var standaloneWorkspace = Assert.IsType<Grid>(main.FindName("StandaloneWorkspace"));
Assert.Equal(Visibility.Visible, accountWorkspace.Visibility);
Assert.Equal(Visibility.Collapsed, standaloneWorkspace.Visibility);
```

Set the visibility values to the opposite state, invoke `ShowAccountWorkspace_Click` through reflection, and assert the account workspace is restored.

- [ ] **Step 2: Run the smoke test and verify RED**

Run:

```powershell
& .\.dotnet\dotnet.exe test tests\SmsWorkbench.Tests\SmsWorkbench.Tests.csproj -c Release --filter DesktopWindowSmokeTests --nologo
```

Expected: failure because the named button and workspaces do not exist.

- [ ] **Step 3: Extend the external launcher contract**

Add to `IFileLauncher`:

```csharp
void OpenUri(Uri uri);
```

Implement it with `Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true })` after requiring an absolute `http` or `https` URI. Update `StubFileLauncher` to record `OpenedUri`.

- [ ] **Step 4: Add sidebar and workspace XAML**

Add namespace:

```xml
xmlns:wv2="clr-namespace:Microsoft.Web.WebView2.Wpf;assembly=Microsoft.Web.WebView2.Wpf"
```

Add a payment navigation button named `StandalonePaymentButton`, with `AutomationProperties.Name="直绑支付"` and `Click="StandalonePayment_Click"`.

Name the existing content grid `AccountWorkspace`. Add sibling `StandaloneWorkspace` in column 1, initially collapsed. Its toolbar contains icon buttons wired to `ShowAccountWorkspace_Click`, `ReloadStandalone_Click`, and `OpenStandaloneExternally_Click`. The content area contains:

```xml
<wv2:WebView2 x:Name="StandaloneWebView"
              Visibility="Collapsed"
              NavigationCompleted="StandaloneWebView_NavigationCompleted"/>
<Grid x:Name="StandaloneLoadingPanel">
  <ProgressBar IsIndeterminate="True" Width="220" Height="4"/>
</Grid>
<Grid x:Name="StandaloneErrorPanel" Visibility="Collapsed">
  <StackPanel HorizontalAlignment="Center" VerticalAlignment="Center">
    <TextBlock x:Name="StandaloneErrorText" TextWrapping="Wrap" TextAlignment="Center"/>
    <Button Content="重试" Click="RetryStandalone_Click"/>
  </StackPanel>
</Grid>
```

- [ ] **Step 5: Implement workspace behavior**

Inject `IStandaloneServiceController` and `IFileLauncher` into `MainWindow`. In `MainWindow.Standalone.cs` implement:

```csharp
private async void StandalonePayment_Click(object sender, RoutedEventArgs e)
{
    AccountWorkspace.Visibility = Visibility.Collapsed;
    StandaloneWorkspace.Visibility = Visibility.Visible;
    await EnsureStandaloneWorkspaceAsync();
}
```

`EnsureStandaloneWorkspaceAsync` must prevent duplicate UI initialization, show loading, call the controller, create `CoreWebView2Environment` using `runtime/webview2`, call `EnsureCoreWebView2Async`, and navigate to `ServiceUri`. Catch `WebView2RuntimeNotFoundException` separately with `未安装 Microsoft Edge WebView2 Runtime。`, catch other exceptions with a concise message, log both, and keep the retry button enabled.

`ShowAccountWorkspace_Click` switches visibility without disposing WebView2. Reload uses `CoreWebView2.Reload()` when ready and otherwise retries startup. External open calls `fileLauncher.OpenUri(controller.ServiceUri)`.

- [ ] **Step 6: Run WPF tests and verify GREEN**

Run the command from Step 2. Expected: WPF smoke tests pass without starting a real 5601 service or initializing WebView2.

- [ ] **Step 7: Commit**

```powershell
git add SmsWorkbench/FileLauncher.cs SmsWorkbench/MainWindow.xaml SmsWorkbench/MainWindow.xaml.cs SmsWorkbench/MainWindow.Standalone.cs tests/SmsWorkbench.Tests/DesktopWindowSmokeTests.cs tests/SmsWorkbench.Tests/TestDoubles.cs
git commit -m "feat: embed standalone payment workspace"
```

### Task 4: Full Verification And Desktop Publish

**Files:**
- Modify: `README.md`
- Modify: `README_STANDALONE.md`

- [ ] **Step 1: Update operating documentation**

Add “直绑支付” to the desktop feature list and state that the left sidebar auto-starts and embeds the local service. Keep the scripts documented as maintenance fallbacks.

- [ ] **Step 2: Run focused .NET tests**

```powershell
& .\.dotnet\dotnet.exe test tests\SmsWorkbench.Tests\SmsWorkbench.Tests.csproj -c Release --filter "ApplicationPathsTests|StandaloneServiceControllerTests|DesktopWindowSmokeTests" --nologo
```

Expected: all focused tests pass.

- [ ] **Step 3: Run the full .NET suite**

```powershell
& .\.dotnet\dotnet.exe test tests\SmsWorkbench.Tests\SmsWorkbench.Tests.csproj -c Release --nologo
```

Expected: zero failures.

- [ ] **Step 4: Run the full Python suite**

```powershell
python -m pytest -q
```

Expected: zero failures; one environment-dependent skip remains acceptable.

- [ ] **Step 5: Publish through the canonical build script**

```powershell
powershell -ExecutionPolicy Bypass -File .\SmsWorkbench\build_dotnet.ps1
```

Expected: `dist/net10/SmsWorkbench.exe`, `Microsoft.Web.WebView2.Wpf.dll`, `WebView2Loader.dll`, and related runtime assets exist.

- [ ] **Step 6: Verify the published desktop surface**

Run the WPF smoke test against the published layout expectations, start `dist/net10/SmsWorkbench.exe`, and confirm the named sidebar entry is visible without overlap at the minimum supported window size. Clicking it must show the embedded workspace and reach `/health` without opening an external browser.

- [ ] **Step 7: Commit and push**

```powershell
git add README.md README_STANDALONE.md
git commit -m "docs: document embedded direct payment workspace"
git push origin main
```

Verify `git ls-remote origin refs/heads/main` equals local `HEAD` and `git status -sb` is clean.
