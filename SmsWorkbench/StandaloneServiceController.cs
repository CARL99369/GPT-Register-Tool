namespace SmsWorkbench
{
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

    public sealed class StandaloneProcessLauncher : IStandaloneProcessLauncher
    {
        public void Start(string launcherPath, string workingDirectory)
        {
            if (!File.Exists(launcherPath))
                throw new FileNotFoundException("Standalone service launcher not found.", launcherPath);

            var startInfo = new ProcessStartInfo
            {
                FileName = "wscript.exe",
                WorkingDirectory = workingDirectory,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            startInfo.ArgumentList.Add(launcherPath);

            if (Process.Start(startInfo) == null)
                throw new InvalidOperationException("Standalone service process did not start.");
        }
    }

    public sealed class StandaloneServiceController : IStandaloneServiceController
    {
        private readonly IApplicationPaths paths;
        private readonly IStandaloneProcessLauncher processLauncher;
        private readonly HttpClient httpClient;
        private readonly TimeSpan pollInterval;
        private readonly int maxProbeAttempts;
        private readonly object startupSync = new();
        private Task<StandaloneServiceResult> startupTask;

        public StandaloneServiceController(
            IApplicationPaths paths,
            IStandaloneProcessLauncher processLauncher,
            HttpClient httpClient,
            TimeSpan pollInterval,
            int maxProbeAttempts)
        {
            this.paths = paths;
            this.processLauncher = processLauncher;
            this.httpClient = httpClient;
            this.pollInterval = pollInterval;
            this.maxProbeAttempts = Math.Max(1, maxProbeAttempts);
        }

        public Uri ServiceUri { get; } = new("http://127.0.0.1:5601/");

        public Task<StandaloneServiceResult> EnsureReadyAsync(CancellationToken cancellationToken = default)
        {
            lock (startupSync)
            {
                if (startupTask == null
                    || (startupTask.IsCompleted && !startupTask.GetAwaiter().GetResult().IsReady))
                    startupTask = EnsureReadyCoreAsync(cancellationToken);

                return startupTask;
            }
        }

        private async Task<StandaloneServiceResult> EnsureReadyCoreAsync(CancellationToken cancellationToken)
        {
            // Let callers arriving in the same UI turn share this startup operation.
            await Task.Yield();
            if (await ProbeHealthAsync(cancellationToken).ConfigureAwait(false))
                return StandaloneServiceResult.Ready();

            if (!File.Exists(paths.StandaloneLauncherPath))
                return StandaloneServiceResult.Failed($"未找到直绑服务启动文件：{paths.StandaloneLauncherPath}");

            try
            {
                processLauncher.Start(paths.StandaloneLauncherPath, paths.RootDirectory);
            }
            catch (Exception ex)
            {
                return StandaloneServiceResult.Failed($"无法启动直绑服务：{ex.Message}");
            }

            for (int attempt = 0; attempt < maxProbeAttempts; attempt++)
            {
                if (pollInterval > TimeSpan.Zero)
                    await Task.Delay(pollInterval, cancellationToken).ConfigureAwait(false);

                if (await ProbeHealthAsync(cancellationToken).ConfigureAwait(false))
                    return StandaloneServiceResult.Ready();
            }

            return StandaloneServiceResult.Failed("直绑服务启动超时，请重试。");
        }

        private async Task<bool> ProbeHealthAsync(CancellationToken cancellationToken)
        {
            try
            {
                using HttpResponseMessage response = await httpClient
                    .GetAsync(new Uri(ServiceUri, "health"), cancellationToken)
                    .ConfigureAwait(false);
                return response.IsSuccessStatusCode;
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                return false;
            }
            catch (HttpRequestException)
            {
                return false;
            }
        }
    }
}
