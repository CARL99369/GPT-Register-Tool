using System.Net;
using SmsWorkbench;

namespace SmsWorkbench.Tests;

public sealed class StandaloneServiceControllerTests
{
    [Fact]
    public async Task HealthyServiceDoesNotStartBackgroundProcess()
    {
        using var fixture = new TemporaryDirectory();
        var launcher = new RecordingStandaloneProcessLauncher();
        using var httpClient = new HttpClient(new SequenceHttpHandler(HttpStatusCode.OK));
        var controller = CreateController(fixture.Path, launcher, httpClient);

        StandaloneServiceResult result = await controller.EnsureReadyAsync();

        Assert.True(result.IsReady);
        Assert.Equal("", result.ErrorMessage);
        Assert.Equal(0, launcher.StartCount);
        Assert.Equal(new Uri("http://127.0.0.1:5601/"), controller.ServiceUri);
    }

    [Fact]
    public async Task OfflineServiceStartsOnceAndWaitsUntilHealthy()
    {
        using var fixture = new TemporaryDirectory();
        File.WriteAllText(Path.Combine(fixture.Path, "run_direct.vbs"), "");
        var launcher = new RecordingStandaloneProcessLauncher();
        using var httpClient = new HttpClient(new SequenceHttpHandler(
            HttpStatusCode.ServiceUnavailable,
            HttpStatusCode.OK));
        var controller = CreateController(fixture.Path, launcher, httpClient);

        StandaloneServiceResult result = await controller.EnsureReadyAsync();

        Assert.True(result.IsReady);
        Assert.Equal(1, launcher.StartCount);
        Assert.Equal(Path.Combine(fixture.Path, "run_direct.vbs"), launcher.LastLauncherPath);
        Assert.Equal(fixture.Path, launcher.LastWorkingDirectory);
    }

    [Fact]
    public async Task ConcurrentCallsShareOneStartupTask()
    {
        using var fixture = new TemporaryDirectory();
        File.WriteAllText(Path.Combine(fixture.Path, "run_direct.vbs"), "");
        var launcher = new RecordingStandaloneProcessLauncher();
        using var httpClient = new HttpClient(new SequenceHttpHandler(
            HttpStatusCode.ServiceUnavailable,
            HttpStatusCode.OK));
        var controller = CreateController(fixture.Path, launcher, httpClient);

        Task<StandaloneServiceResult> first = controller.EnsureReadyAsync();
        Task<StandaloneServiceResult> second = controller.EnsureReadyAsync();

        Assert.Same(first, second);
        StandaloneServiceResult[] results = await Task.WhenAll(first, second);
        Assert.All(results, result => Assert.True(result.IsReady));
        Assert.Equal(1, launcher.StartCount);
    }

    [Fact]
    public async Task MissingLauncherReturnsActionableFailure()
    {
        using var fixture = new TemporaryDirectory();
        var launcher = new RecordingStandaloneProcessLauncher();
        using var httpClient = new HttpClient(new SequenceHttpHandler(HttpStatusCode.ServiceUnavailable));
        var controller = CreateController(fixture.Path, launcher, httpClient);

        StandaloneServiceResult result = await controller.EnsureReadyAsync();

        Assert.False(result.IsReady);
        Assert.Contains("未找到直绑服务启动文件", result.ErrorMessage);
        Assert.Contains(Path.Combine(fixture.Path, "run_direct.vbs"), result.ErrorMessage);
        Assert.Equal(0, launcher.StartCount);
    }

    [Fact]
    public async Task ProbeExhaustionReturnsTimeoutFailure()
    {
        using var fixture = new TemporaryDirectory();
        File.WriteAllText(Path.Combine(fixture.Path, "run_direct.vbs"), "");
        var launcher = new RecordingStandaloneProcessLauncher();
        using var httpClient = new HttpClient(new SequenceHttpHandler(
            HttpStatusCode.ServiceUnavailable,
            HttpStatusCode.ServiceUnavailable,
            HttpStatusCode.ServiceUnavailable));
        var controller = CreateController(fixture.Path, launcher, httpClient, maxProbeAttempts: 2);

        StandaloneServiceResult result = await controller.EnsureReadyAsync();

        Assert.False(result.IsReady);
        Assert.Equal("直绑服务启动超时，请重试。", result.ErrorMessage);
        Assert.Equal(1, launcher.StartCount);
    }

    private static StandaloneServiceController CreateController(
        string rootDirectory,
        IStandaloneProcessLauncher launcher,
        HttpClient httpClient,
        int maxProbeAttempts = 3)
        => new(
            new TestApplicationPaths(rootDirectory),
            launcher,
            httpClient,
            pollInterval: TimeSpan.Zero,
            maxProbeAttempts: maxProbeAttempts);

    private sealed class RecordingStandaloneProcessLauncher : IStandaloneProcessLauncher
    {
        public int StartCount { get; private set; }
        public string LastLauncherPath { get; private set; } = "";
        public string LastWorkingDirectory { get; private set; } = "";

        public void Start(string launcherPath, string workingDirectory)
        {
            StartCount++;
            LastLauncherPath = launcherPath;
            LastWorkingDirectory = workingDirectory;
        }
    }

    private sealed class SequenceHttpHandler : HttpMessageHandler
    {
        private readonly Queue<HttpStatusCode> statuses;
        private readonly object sync = new();

        public SequenceHttpHandler(params HttpStatusCode[] statuses)
        {
            this.statuses = new Queue<HttpStatusCode>(statuses);
        }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            Assert.Equal(new Uri("http://127.0.0.1:5601/health"), request.RequestUri);
            lock (sync)
            {
                HttpStatusCode status = statuses.Count > 1
                    ? statuses.Dequeue()
                    : statuses.Peek();
                return Task.FromResult(new HttpResponseMessage(status));
            }
        }
    }
}
