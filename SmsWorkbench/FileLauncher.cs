namespace SmsWorkbench
{
    public interface IFileLauncher
    {
        bool Exists(string path);
        void Open(string path);
        void OpenUri(Uri uri);
    }

    public sealed class FileLauncher : IFileLauncher
    {
        public bool Exists(string path) => File.Exists(path) || Directory.Exists(path);

        public void Open(string path)
        {
            if (!Exists(path)) return;
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
        }

        public void OpenUri(Uri uri)
        {
            if (uri == null || !uri.IsAbsoluteUri || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
                throw new ArgumentException("Only absolute HTTP or HTTPS addresses can be opened.", nameof(uri));

            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
        }
    }
}
