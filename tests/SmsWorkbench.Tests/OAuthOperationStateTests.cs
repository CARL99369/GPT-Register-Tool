using Xunit;

namespace SmsWorkbench.Tests;

public sealed class OAuthOperationStateTests
{
    [Fact]
    public void FailedRefreshIsDisplayedEvenWhenAccountKeepsOldTokens()
    {
        Assert.Equal("RT获取失败", OAuthOperationState.Display("false", "authorize_continue_failed:409"));
    }

    [Fact]
    public void SuccessfulRefreshIsDisplayedAsLatestOperation()
    {
        Assert.Equal("RT获取成功", OAuthOperationState.Display("true", ""));
    }
}
