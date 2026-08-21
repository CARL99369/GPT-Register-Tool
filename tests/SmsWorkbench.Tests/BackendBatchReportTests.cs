using System.Text.Json;
using SmsWorkbench;

namespace SmsWorkbench.Tests;

public sealed class BackendBatchReportTests
{
    [Fact]
    public void BuildsPerAccountSummaryFromBackendResults()
    {
        JsonElement payload = JsonDocument.Parse(
            """
            {
              "ok": false,
              "total": 5,
              "success": 4,
              "failed": 1,
              "results": [
                {"ok": true, "email": "one@example.com", "refresh_token_status": "oauth_present"},
                {"ok": true, "email": "two@example.com"},
                {"ok": true, "email": "three@example.com"},
                {"ok": true, "email": "four@example.com"},
                {"ok": false, "email": "five@example.com", "error": "authorize_continue_failed:403"}
              ]
            }
            """).RootElement.Clone();
        var result = new BackendCommandResult(3, "", "", payload, TimedOut: false);

        BackendBatchReport? report = BackendBatchReportBuilder.TryBuild(
            "一键接码(5)",
            result,
            new DateTimeOffset(2026, 8, 21, 12, 0, 0, TimeSpan.Zero),
            new DateTimeOffset(2026, 8, 21, 12, 1, 0, TimeSpan.Zero));

        Assert.NotNull(report);
        Assert.Equal(5, report.Total);
        Assert.Equal(4, report.Succeeded);
        Assert.Equal(1, report.Failed);
        Assert.Equal("成功", report.Items[0].Result);
        Assert.Equal("oauth_present", report.Items[0].Reason);
        Assert.Equal("失败", report.Items[4].Result);
        Assert.Equal("authorize_continue_failed:403", report.Items[4].Reason);
    }

    [Fact]
    public void BuildsFallbackSummaryFromTerminalProgressEvents()
    {
        var result = new BackendCommandResult(0, "plain output", "", null, TimedOut: false);
        var events = new[]
        {
            Terminal("registration", "one@example.com", "completed", "success", ""),
            Terminal("registration", "two@example.com", "failed", "failed", "email_otp_poll_timeout"),
        };

        BackendBatchReport? report = BackendBatchReportBuilder.TryBuild(
            "一键注册",
            result,
            DateTimeOffset.UtcNow.AddMinutes(-1),
            DateTimeOffset.UtcNow,
            events);

        Assert.NotNull(report);
        Assert.Equal(2, report.Total);
        Assert.Equal(1, report.Succeeded);
        Assert.Equal(1, report.Failed);
        Assert.Equal("email_otp_poll_timeout", report.Items[1].Reason);
    }

    [Fact]
    public void IgnoresNonBatchPayloads()
    {
        JsonElement payload = JsonDocument.Parse("{\"ok\":true,\"url\":\"https://example.com\"}")
            .RootElement.Clone();
        var result = new BackendCommandResult(0, "", "", payload, TimedOut: false);

        BackendBatchReport? report = BackendBatchReportBuilder.TryBuild(
            "打开支付链接",
            result,
            DateTimeOffset.UtcNow,
            DateTimeOffset.UtcNow);

        Assert.Null(report);
    }

    [Fact]
    public void SavesReportAsJson()
    {
        string root = Path.Combine(Path.GetTempPath(), "smsworkbench-report-" + Guid.NewGuid().ToString("N"));
        try
        {
            var report = new BackendBatchReport(
                "一键接码(1)",
                DateTimeOffset.UtcNow.AddSeconds(-5),
                DateTimeOffset.UtcNow,
                1,
                0,
                1,
                3,
                new[] { new BackendBatchReportItem("one@example.com", "失败", "network_error", false) });

            string path = BackendBatchReportStore.Save(root, report);

            Assert.True(File.Exists(path));
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
            Assert.Equal(1, document.RootElement.GetProperty("total").GetInt32());
            Assert.Equal("network_error", document.RootElement.GetProperty("items")[0].GetProperty("reason").GetString());
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static BackendProgressEvent Terminal(
        string domain,
        string account,
        string stage,
        string status,
        string detail)
        => new(
            domain,
            "run",
            account,
            "",
            stage,
            status,
            detail,
            Terminal: true,
            AccountTerminal: true);
}
