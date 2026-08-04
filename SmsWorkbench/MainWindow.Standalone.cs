using Microsoft.Web.WebView2.Core;

namespace SmsWorkbench
{
    public partial class MainWindow
    {
        private readonly string standaloneWebViewDataDirectory;
        private bool standaloneStarting;
        private bool standaloneWebViewReady;

        private async void StandalonePayment_Click(object sender, RoutedEventArgs e)
        {
            AccountWorkspace.Visibility = Visibility.Collapsed;
            StandaloneWorkspace.Visibility = Visibility.Visible;
            await EnsureStandaloneWorkspaceAsync();
        }

        private void ShowAccountWorkspace_Click(object sender, RoutedEventArgs e)
        {
            StandaloneWorkspace.Visibility = Visibility.Collapsed;
            AccountWorkspace.Visibility = Visibility.Visible;
        }

        private async void RetryStandalone_Click(object sender, RoutedEventArgs e)
        {
            standaloneWebViewReady = false;
            await EnsureStandaloneWorkspaceAsync();
        }

        private async void ReloadStandalone_Click(object sender, RoutedEventArgs e)
        {
            if (StandaloneWebView.CoreWebView2 == null)
            {
                standaloneWebViewReady = false;
                await EnsureStandaloneWorkspaceAsync();
                return;
            }

            ShowStandaloneLoading("正在重新加载...");
            StandaloneWebView.CoreWebView2.Reload();
        }

        private void OpenStandaloneExternally_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                fileLauncher.OpenUri(standaloneServiceController.ServiceUri);
            }
            catch (Exception ex)
            {
                ShowStandaloneError($"无法打开浏览器：{ex.Message}");
            }
        }

        private async Task EnsureStandaloneWorkspaceAsync()
        {
            if (standaloneStarting)
                return;

            if (standaloneWebViewReady && StandaloneWebView.CoreWebView2 != null)
            {
                ShowStandaloneReady();
                return;
            }

            standaloneStarting = true;
            ShowStandaloneLoading("正在启动直绑服务...");
            try
            {
                StandaloneServiceResult result = await standaloneServiceController.EnsureReadyAsync();
                if (!result.IsReady)
                {
                    ShowStandaloneError(result.ErrorMessage);
                    return;
                }

                Directory.CreateDirectory(standaloneWebViewDataDirectory);
                CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(
                    userDataFolder: standaloneWebViewDataDirectory);
                await StandaloneWebView.EnsureCoreWebView2Async(environment);
                StandaloneWebView.CoreWebView2.Navigate(standaloneServiceController.ServiceUri.AbsoluteUri);
                StandaloneStatusText.Text = "正在加载页面";
            }
            catch (WebView2RuntimeNotFoundException ex)
            {
                logger.Error(ex, "Microsoft Edge WebView2 Runtime is not installed");
                ShowStandaloneError("未安装 Microsoft Edge WebView2 Runtime。");
            }
            catch (Exception ex)
            {
                logger.Error(ex, "Failed to initialize standalone payment workspace");
                ShowStandaloneError($"直绑支付页面启动失败：{ex.Message}");
            }
            finally
            {
                standaloneStarting = false;
            }
        }

        private void StandaloneWebView_NavigationCompleted(
            object sender,
            CoreWebView2NavigationCompletedEventArgs e)
        {
            if (!e.IsSuccess)
            {
                standaloneWebViewReady = false;
                ShowStandaloneError($"直绑支付页面加载失败：{e.WebErrorStatus}");
                return;
            }

            standaloneWebViewReady = true;
            ShowStandaloneReady();
        }

        private void ShowStandaloneLoading(string status)
        {
            StandaloneStatusText.Text = status;
            StandaloneLoadingText.Text = status;
            StandaloneWebView.Visibility = Visibility.Hidden;
            StandaloneLoadingPanel.Visibility = Visibility.Visible;
            StandaloneErrorPanel.Visibility = Visibility.Collapsed;
        }

        private void ShowStandaloneReady()
        {
            StandaloneStatusText.Text = "已连接";
            StandaloneLoadingPanel.Visibility = Visibility.Collapsed;
            StandaloneErrorPanel.Visibility = Visibility.Collapsed;
            StandaloneWebView.Visibility = Visibility.Visible;
        }

        private void ShowStandaloneError(string message)
        {
            string detail = string.IsNullOrWhiteSpace(message) ? "直绑支付服务不可用。" : message.Trim();
            StandaloneStatusText.Text = "连接失败";
            StandaloneErrorText.Text = detail;
            StandaloneWebView.Visibility = Visibility.Hidden;
            StandaloneLoadingPanel.Visibility = Visibility.Collapsed;
            StandaloneErrorPanel.Visibility = Visibility.Visible;
            Log(detail);
        }
    }
}
