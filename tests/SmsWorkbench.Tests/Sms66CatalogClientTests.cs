using System.Net;
using System.Text;

namespace SmsWorkbench.Tests;

public sealed class Sms66CatalogClientTests
{
    [Fact]
    public async Task LoadsAvailableNumbersForProject480()
    {
        var handler = new StubHandler("""
            {"sta":"ok","data":{"total":2,"list":[
              {"phone":"12025550101","expiration_date":"2026-09-01 00:00:00"},
              {"phone":"14155550123","expiration_date":"2026-09-02 00:00:00"}
            ]}}
            """);
        using var client = new HttpClient(handler);

        IReadOnlyList<Sms66PhoneChoice> choices = await Sms66CatalogClient.LoadAvailableNumbersAsync(
            client, "secret", "https://app.yuntl.cc", "480");

        Assert.Equal(2, choices.Count);
        Assert.Equal("+14155550123", choices[1].Phone);
        Assert.Equal("1415", choices[1].Prefix);
        Assert.Contains("app_id=480", handler.LastRequestUri?.Query);
    }

    [Fact]
    public async Task DetectsProjectThatCannotUseDesignatedPurchase()
    {
        var handler = new StubHandler("""{"sta":"fail","msg":"项目无效或无权购买"}""");
        using var client = new HttpClient(handler);

        var error = await Assert.ThrowsAsync<InvalidDataException>(() =>
            Sms66CatalogClient.LoadAvailableNumbersAsync(
                client, "secret", "https://app.yuntl.cc", "480"));

        Assert.True(Sms66CatalogClient.IsDesignatedPurchaseUnavailable(error));
    }

    [Fact]
    public void DoesNotTreatNetworkErrorsAsDesignatedPurchaseFallback()
    {
        Assert.False(Sms66CatalogClient.IsDesignatedPurchaseUnavailable(
            new HttpRequestException("connection refused")));
    }

    private sealed class StubHandler(string json) : HttpMessageHandler
    {
        public Uri? LastRequestUri { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            LastRequestUri = request.RequestUri;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json")
            });
        }
    }
}
