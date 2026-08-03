using SmsWorkbench;

namespace SmsWorkbench.Tests;

public sealed class MailboxLineParserTests
{
    [Theory]
    [InlineData("user@icloud.com----https://mail.example.test/messages/key/user%40icloud.com", "url_html", "--chatai-mailbox-file")]
    [InlineData("user@icloud.com----https://mail.example.test/inbox?a=one----two", "url_html", "--chatai-mailbox-file")]
    [InlineData("user@hotmail.com----pw----client----refresh", "chatai", "--chatai-mailbox-file")]
    [InlineData("user@hotmail.com---pw---refresh", "graph", "--mailbox-file")]
    [InlineData("gmail://user@gmail.com---app-password", "gmail", "--mailbox-file")]
    [InlineData("remail://user@example.com---token---order", "remail", "--mailbox-file")]
    public void ClassifiesSupportedMailboxLines(string line, string provider, string argument)
    {
        Assert.True(MailboxLineParser.TryParse(line, out MailboxLineInfo info));
        Assert.Equal(provider, info.Provider);
        Assert.Equal(argument, info.CommandArgument);
    }

    [Theory]
    [InlineData("user@icloud.com----file:///mail.html")]
    [InlineData("user@icloud.com----javascript:alert(1)")]
    [InlineData("not-an-email----https://mail.example.test/inbox")]
    [InlineData("user@icloud.com----http:///missing-host")]
    public void RejectsInvalidTwoFieldUrlMailboxLines(string line)
    {
        Assert.False(MailboxLineParser.TryParse(line, out _));
    }
}
