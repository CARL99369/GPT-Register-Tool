using SmsWorkbench;

namespace SmsWorkbench.Tests;

internal sealed class TestApplicationPaths : IApplicationPaths
{
    public TestApplicationPaths(string rootDirectory)
    {
        RootDirectory = rootDirectory;
        BackendScriptPath = Path.Combine(rootDirectory, "chatgpt_phone_reg.py");
        StandaloneLauncherPath = Path.Combine(rootDirectory, "run_direct.vbs");
        StandaloneWebViewDataDirectory = Path.Combine(rootDirectory, "runtime", "webview2");
    }

    public string RootDirectory { get; }

    public string BackendScriptPath { get; }

    public string StandaloneLauncherPath { get; }

    public string StandaloneWebViewDataDirectory { get; }
}

internal sealed class StubBackendClient : IBackendClient
{
    public Func<BackendCommand, BackendCommandResult> Handler { get; set; } = _ =>
        new BackendCommandResult(0, "", "", null, false);

    public Func<BackendCommand, CancellationToken, Task<BackendCommandResult>>? AsyncHandler { get; set; }

    public Action<IProgress<BackendOutputLine>?>? ReportProgress { get; set; }

    public BackendCommand? LastCommand { get; private set; }

    public List<BackendCommand> Commands { get; } = new();

    public async Task<BackendCommandResult> RunAsync(
        BackendCommand command,
        IProgress<BackendOutputLine>? progress = null,
        CancellationToken cancellationToken = default)
    {
        LastCommand = command;
        Commands.Add(command);
        cancellationToken.ThrowIfCancellationRequested();
        ReportProgress?.Invoke(progress);
        if (AsyncHandler != null)
            return await AsyncHandler(command, cancellationToken);
        return Handler(command);
    }
}

internal sealed class StubFileLauncher : IFileLauncher
{
    public string OpenedPath { get; private set; } = "";
    public Uri? OpenedUri { get; private set; }

    public bool Exists(string path) => !string.IsNullOrWhiteSpace(path);

    public void Open(string path) => OpenedPath = path;

    public void OpenUri(Uri uri) => OpenedUri = uri;
}

internal sealed class StubStandaloneServiceController : IStandaloneServiceController
{
    public Uri ServiceUri { get; init; } = new("http://127.0.0.1:5601/");

    public StandaloneServiceResult Result { get; set; } = StandaloneServiceResult.Ready();

    public int EnsureReadyCallCount { get; private set; }

    public Task<StandaloneServiceResult> EnsureReadyAsync(CancellationToken cancellationToken = default)
    {
        EnsureReadyCallCount++;
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(Result);
    }
}
