using System.IO;

namespace SmsWorkbench
{
    public interface IApplicationPaths
    {
        string RootDirectory { get; }
        string BackendScriptPath { get; }
        string StandaloneLauncherPath { get; }
        string StandaloneWebViewDataDirectory { get; }
    }

    public sealed class ApplicationPaths : IApplicationPaths
    {
        public ApplicationPaths(string baseDirectory)
        {
            RootDirectory = FindRepositoryRoot(baseDirectory);
            BackendScriptPath = Path.Combine(RootDirectory, "chatgpt_phone_reg.py");
            StandaloneLauncherPath = Path.Combine(RootDirectory, "run_direct.vbs");
            StandaloneWebViewDataDirectory = Path.Combine(RootDirectory, "runtime", "webview2");
        }

        public string RootDirectory { get; }

        public string BackendScriptPath { get; }

        public string StandaloneLauncherPath { get; }

        public string StandaloneWebViewDataDirectory { get; }

        private static string FindRepositoryRoot(string baseDirectory)
        {
            var current = new DirectoryInfo(Path.GetFullPath(baseDirectory));
            for (var depth = 0; current != null && depth < 10; depth++, current = current.Parent)
            {
                if (File.Exists(Path.Combine(current.FullName, "chatgpt_phone_reg.py")))
                    return current.FullName;
            }

            return Path.GetFullPath(baseDirectory);
        }
    }
}
