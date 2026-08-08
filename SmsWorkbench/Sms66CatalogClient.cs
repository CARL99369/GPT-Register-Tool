namespace SmsWorkbench
{
    internal static class Sms66CatalogClient
    {
        internal const string DefaultEndpoint = "https://app.yuntl.cc";
        internal const string OpenAiProjectId = "480";

        internal static bool IsDesignatedPurchaseUnavailable(Exception exception)
        {
            return exception is InvalidDataException
                && exception.Message.Contains("项目无效或无权购买", StringComparison.Ordinal);
        }

        internal static async Task<IReadOnlyList<Sms66PhoneChoice>> LoadAvailableNumbersAsync(
            HttpClient httpClient,
            string apiKey,
            string endpoint,
            string appId = OpenAiProjectId)
        {
            string url = endpoint.TrimEnd('/') + "/api/designated_available_phones"
                + "?api_key=" + Uri.EscapeDataString(apiKey)
                + "&app_id=" + Uri.EscapeDataString(appId)
                + "&limit=2000&offset=0";
            using HttpResponseMessage response = await httpClient.GetAsync(url);
            string body = await response.Content.ReadAsStringAsync();
            response.EnsureSuccessStatusCode();

            using JsonDocument document = JsonDocument.Parse(body);
            JsonElement root = document.RootElement;
            string status = JsonString(root, "sta");
            if (!status.Equals("ok", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException(JsonString(root, "msg", "SMS66 返回失败"));
            }
            if (!root.TryGetProperty("data", out JsonElement data)
                || !data.TryGetProperty("list", out JsonElement list)
                || list.ValueKind != JsonValueKind.Array)
            {
                return Array.Empty<Sms66PhoneChoice>();
            }

            var result = new List<Sms66PhoneChoice>();
            foreach (JsonElement item in list.EnumerateArray())
            {
                string phone = NormalizePhone(JsonString(item, "phone"));
                if (phone.Length == 0) continue;
                result.Add(new Sms66PhoneChoice(phone, JsonString(item, "expiration_date")));
            }
            return result;
        }

        private static string NormalizePhone(string value)
        {
            string digits = new((value ?? "").Where(char.IsDigit).ToArray());
            return digits.Length == 0 ? "" : "+" + digits;
        }

        private static string JsonString(JsonElement element, string name, string fallback = "")
        {
            if (element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out JsonElement value))
                return value.ValueKind == JsonValueKind.String ? value.GetString() ?? fallback : value.ToString();
            return fallback;
        }
    }

    internal sealed class Sms66PhoneChoice
    {
        internal Sms66PhoneChoice(string phone, string expirationDate)
        {
            Phone = phone;
            ExpirationDate = expirationDate ?? "";
            string digits = new(phone.Where(char.IsDigit).ToArray());
            Prefix = digits.Length <= 4 ? digits : digits[..4];
        }

        public string Phone { get; }
        public string Prefix { get; }
        public string ExpirationDate { get; }
        public string DisplayName => string.IsNullOrWhiteSpace(ExpirationDate)
            ? Phone
            : $"{Phone} · 到期 {ExpirationDate}";
    }
}
