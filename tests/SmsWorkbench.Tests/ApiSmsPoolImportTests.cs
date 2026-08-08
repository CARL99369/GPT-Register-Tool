using System.Text.Json;
using SmsWorkbench;

namespace SmsWorkbench.Tests;

public sealed class ApiSmsPoolImportTests
{
    [Fact]
    public void ParsesDocumentedPhoneUrlFormat()
    {
        const string input = "19862940168---http://sms66.vip/apisms/token";

        Assert.True(ApiSmsPoolImport.TryParse(input, out var entries, out string error), error);
        ApiSmsPoolEntry entry = Assert.Single(entries);
        Assert.Equal("+19862940168", entry.Phone);
        Assert.Equal("http://sms66.vip/apisms/token", entry.SmsApiUrl);
    }

    [Fact]
    public void SplitsOnlyOnFirstTripleDashAndRemovesExactDuplicates()
    {
        const string input = "19862940168---https://example.test/a---b?x=1\r\n"
            + "19862940168---https://example.test/a---b?x=1\n"
            + "+44 (20) 7946 0958---https://example.test/second";

        Assert.True(ApiSmsPoolImport.TryParse(input, out var entries, out string error), error);
        Assert.Collection(
            entries,
            first =>
            {
                Assert.Equal("+19862940168", first.Phone);
                Assert.Equal("https://example.test/a---b?x=1", first.SmsApiUrl);
            },
            second =>
            {
                Assert.Equal("+442079460958", second.Phone);
                Assert.Equal("https://example.test/second", second.SmsApiUrl);
            });
    }

    [Theory]
    [InlineData("19862940168--https://example.test/code", "缺少 --- 分隔符")]
    [InlineData("19862940168---file:///code", "URL 无效")]
    [InlineData("abc---https://example.test/code", "号码无效")]
    [InlineData("123---https://example.test/code", "号码无效")]
    [InlineData("19862940168---http:///missing-host", "URL 无效")]
    [InlineData("", "至少导入一条")]
    public void RejectsMalformedLines(string input, string expectedMessage)
    {
        Assert.False(ApiSmsPoolImport.TryParse(input, out var entries, out string error));
        Assert.Empty(entries);
        Assert.Contains(expectedMessage, error);
    }

    [Fact]
    public void ReportsEveryInvalidLineBeforeLaunch()
    {
        const string input = "abc---https://example.test/code\n19862940168---file:///code";

        Assert.False(ApiSmsPoolImport.TryParse(input, out _, out string error));
        Assert.Contains("第 1 行号码无效", error);
        Assert.Contains("第 2 行 URL 无效", error);
    }

    [Fact]
    public void WritesBackendJsonContractToTemporaryFile()
    {
        var entries = new[]
        {
            new ApiSmsPoolEntry("+19862940168", "http://sms66.vip/apisms/token")
        };

        string path = ApiSmsPoolImport.WriteTemporaryFile(entries);
        try
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
            JsonElement item = Assert.Single(document.RootElement.EnumerateArray());
            Assert.Equal("+19862940168", item.GetProperty("phone").GetString());
            Assert.Equal("http://sms66.vip/apisms/token", item.GetProperty("sms_api_url").GetString());
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void AddsProcessScopedPhonePoolArguments()
    {
        var args = new List<string> { "--one-click-sms" };

        ApiSmsPoolImport.AddBackendArguments(args, @"C:\Temp\pool.json");

        Assert.Collection(
            args,
            value => Assert.Equal("--one-click-sms", value),
            value => Assert.Equal("--phone-source", value),
            value => Assert.Equal("phone_pool", value),
            value => Assert.Equal("--phone-pool-file", value),
            value => Assert.Equal(@"C:\Temp\pool.json", value));
    }
}
