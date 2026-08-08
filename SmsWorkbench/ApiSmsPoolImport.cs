using System.Text.Json.Serialization;

namespace SmsWorkbench
{
    internal sealed record ApiSmsPoolEntry(
        [property: JsonPropertyName("phone")] string Phone,
        [property: JsonPropertyName("sms_api_url")] string SmsApiUrl);

    internal static class ApiSmsPoolImport
    {
        internal static bool TryParse(
            string text,
            out IReadOnlyList<ApiSmsPoolEntry> entries,
            out string error)
        {
            var parsed = new List<ApiSmsPoolEntry>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            var errors = new List<string>();
            string[] lines = (text ?? "")
                .Replace("\r\n", "\n", StringComparison.Ordinal)
                .Replace('\r', '\n')
                .Split('\n');

            for (int index = 0; index < lines.Length; index++)
            {
                string line = lines[index].Trim();
                if (line.Length == 0) continue;

                int separator = line.IndexOf("---", StringComparison.Ordinal);
                if (separator <= 0)
                {
                    errors.Add($"第 {index + 1} 行缺少 --- 分隔符");
                    continue;
                }

                string rawPhone = line.Substring(0, separator).Trim();
                string digits = new(rawPhone.Where(char.IsDigit).ToArray());
                bool hasInvalidPhoneCharacter = rawPhone.Any(character =>
                    !char.IsDigit(character)
                    && character != '+'
                    && character != '-'
                    && character != '('
                    && character != ')'
                    && !char.IsWhiteSpace(character));
                if (hasInvalidPhoneCharacter || digits.Length is < 7 or > 15)
                {
                    errors.Add($"第 {index + 1} 行号码无效");
                    continue;
                }

                string url = line.Substring(separator + 3).Trim();
                if (!Uri.TryCreate(url, UriKind.Absolute, out Uri? uri)
                    || uri.Host.Length == 0
                    || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
                {
                    errors.Add($"第 {index + 1} 行 URL 无效");
                    continue;
                }

                var entry = new ApiSmsPoolEntry("+" + digits, url);
                if (seen.Add(entry.Phone + "\n" + entry.SmsApiUrl))
                    parsed.Add(entry);
            }

            if (errors.Count == 0 && parsed.Count == 0)
                errors.Add("至少导入一条号码---URL 记录");

            if (errors.Count > 0)
            {
                entries = Array.Empty<ApiSmsPoolEntry>();
                error = string.Join(Environment.NewLine, errors);
                return false;
            }

            entries = parsed;
            error = "";
            return true;
        }

        internal static string WriteTemporaryFile(IReadOnlyList<ApiSmsPoolEntry> entries)
        {
            if (entries == null || entries.Count == 0)
                throw new ArgumentException("API SMS pool must contain at least one entry.", nameof(entries));

            string path = Path.Combine(
                Path.GetTempPath(),
                "api_sms_pool_" + Guid.NewGuid().ToString("N") + ".json");
            string json = JsonSerializer.Serialize(entries);
            File.WriteAllText(path, json, new UTF8Encoding(false));
            return path;
        }

        internal static void AddBackendArguments(List<string> args, string path)
        {
            ArgumentNullException.ThrowIfNull(args);
            if (string.IsNullOrWhiteSpace(path))
                throw new ArgumentException("Phone pool file path is required.", nameof(path));

            args.Add("--phone-source");
            args.Add("phone_pool");
            args.Add("--phone-pool-file");
            args.Add(path);
        }
    }
}
