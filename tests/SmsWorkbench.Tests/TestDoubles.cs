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

    public BackendCommand? LastCommand { get; private set; }

    public Task<BackendCommandResult> RunAsync(
        BackendCommand command,
        IProgress<BackendOutputLine>? progress = null,
        CancellationToken cancellationToken = default)
    {
        LastCommand = command;
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(Handler(command));
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
