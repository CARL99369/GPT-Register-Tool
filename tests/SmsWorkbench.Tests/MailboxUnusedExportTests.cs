using SmsWorkbench;

namespace SmsWorkbench.Tests;

public sealed class MailboxUnusedExportTests
{
    [Theory]
    [InlineData("Chatai邮箱池", "已授权", "", false, true, true)]
    [InlineData("邮箱池", "可收信", "", false, true, true)]
    [InlineData("ReMail邮箱池", "可收信", "", false, true, true)]
    [InlineData("Chatai邮箱池", "已注册", "", false, true, false)]
    [InlineData("Session", "已授权", "", false, true, false)]
    [InlineData("SQLite账号", "可收信", "", true, true, false)]
    [InlineData("Chatai邮箱池", "待支付", "pending", false, true, false)]
    [InlineData("Chatai邮箱池", "已授权", "", false, false, false)]
    public void ClassifiesUnusedMailboxRows(
        string accountType,
        string status,
        string payPalStatus,
        bool hasAccessToken,
        bool hasCredential,
        bool expected)
    {
        Assert.Equal(
            expected,
            MailboxUnusedExport.IsUnusedMailboxRow(accountType, status, payPalStatus, hasAccessToken, hasCredential));
    }

    [Fact]
    public void ResolveExportLinePrefersMailboxLine()
    {
        string line = MailboxUnusedExport.ResolveExportLine(
            "user@example.com----pw----client----refresh",
            "fallback----line",
            value => value.Contains("----", StringComparison.Ordinal));

        Assert.Equal("user@example.com----pw----client----refresh", line);
    }

    [Fact]
    public void CollectExportLinesDedupsByEmailAndLine()
    {
        var rows = new[]
        {
            ("User@Example.com", "user@example.com----pw----client----refresh"),
            ("user@example.com", "user@example.com----pw----client----refresh"),
            ("other@example.com", "other@example.com----pw----client----refresh"),
            ("", ""),
        };

        IReadOnlyList<string> lines = MailboxUnusedExport.CollectExportLines(rows, out int skipped);

        Assert.Equal(2, lines.Count);
        Assert.Equal(2, skipped);
        Assert.Contains("user@example.com----pw----client----refresh", lines);
        Assert.Contains("other@example.com----pw----client----refresh", lines);
    }
}
