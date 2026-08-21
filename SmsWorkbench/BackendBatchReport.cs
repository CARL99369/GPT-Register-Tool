using System.Text.Json.Serialization;

namespace SmsWorkbench;

public sealed record BackendBatchReportItem(
    string Account,
    string Result,
    string Reason,
    bool Succeeded);

public sealed record BackendBatchReport(
    string TaskName,
    DateTimeOffset StartedAt,
    DateTimeOffset FinishedAt,
    int Total,
    int Succeeded,
    int Failed,
    int ExitCode,
    IReadOnlyList<BackendBatchReportItem> Items)
{
    [JsonIgnore]
    public string Summary => $"共 {Total} 个，成功 {Succeeded} 个，失败 {Failed} 个";

    [JsonIgnore]
    public string DurationText
    {
        get
        {
            int seconds = Math.Max(0, (int)(FinishedAt - StartedAt).TotalSeconds);
            return seconds < 60 ? $"{seconds} 秒" : $"{seconds / 60} 分 {seconds % 60} 秒";
        }
    }

    public string ToClipboardText()
    {
        var lines = new List<string>
        {
            TaskName,
            $"{Summary}，耗时 {DurationText}",
        };
        lines.AddRange(Items.Select(item => $"{item.Account}\t{item.Result}\t{item.Reason}"));
        return string.Join(Environment.NewLine, lines);
    }
}

public static class BackendBatchReportBuilder
{
    public static BackendBatchReport? TryBuild(
        string taskName,
        BackendCommandResult result,
        DateTimeOffset startedAt,
        DateTimeOffset finishedAt,
        IEnumerable<BackendProgressEvent>? progressEvents = null)
    {
        var items = result.Payload.HasValue
            ? ParseResultItems(result.Payload.Value)
            : new List<BackendBatchReportItem>();

        if (items.Count == 0)
            items = ParseProgressItems(progressEvents);

        int total = items.Count;
        int succeeded = items.Count(item => item.Succeeded);
        int failed = items.Count - succeeded;

        if (result.Payload.HasValue && result.Payload.Value.ValueKind == JsonValueKind.Object)
        {
            JsonElement payload = result.Payload.Value;
            total = Number(payload, "total", total);
            succeeded = Number(payload, "success", succeeded);
            failed = Number(payload, "failed", failed);
        }

        if (items.Count == 0 && total <= 0)
            return null;

        if (total <= 0)
            total = Math.Max(items.Count, succeeded + failed);
        if (succeeded < 0)
            succeeded = 0;
        if (failed < 0)
            failed = 0;

        return new BackendBatchReport(
            taskName ?? "",
            startedAt,
            finishedAt,
            total,
            succeeded,
            failed,
            result.ExitCode,
            items);
    }

    private static List<BackendBatchReportItem> ParseResultItems(JsonElement payload)
    {
        var output = new List<BackendBatchReportItem>();
        if (payload.ValueKind != JsonValueKind.Object
            || !payload.TryGetProperty("results", out JsonElement results)
            || results.ValueKind != JsonValueKind.Array)
            return output;

        int index = 0;
        foreach (JsonElement row in results.EnumerateArray())
        {
            index++;
            if (row.ValueKind != JsonValueKind.Object)
                continue;
            bool succeeded = ResultSucceeded(row);
            string account = FirstText(row, "email", "account_ref", "identifier", "name", "phone");
            if (account.Length == 0)
                account = "#" + index.ToString(CultureInfo.InvariantCulture);
            string reason = succeeded
                ? FirstText(row, "refresh_token_status", "status", "decision", "message")
                : FirstError(row);
            if (reason.Length == 0)
                reason = succeeded ? "成功" : "未返回失败原因";
            output.Add(new BackendBatchReportItem(
                SensitiveDataSanitizer.Redact(account),
                succeeded ? "成功" : "失败",
                SensitiveDataSanitizer.Redact(reason),
                succeeded));
        }
        return output;
    }

    private static List<BackendBatchReportItem> ParseProgressItems(IEnumerable<BackendProgressEvent>? events)
    {
        var byAccount = new Dictionary<string, BackendBatchReportItem>(StringComparer.OrdinalIgnoreCase);
        foreach (BackendProgressEvent progress in events ?? Array.Empty<BackendProgressEvent>())
        {
            if (!progress.Terminal || string.IsNullOrWhiteSpace(progress.AccountRef))
                continue;
            bool succeeded = string.Equals(progress.Status, "success", StringComparison.OrdinalIgnoreCase)
                || string.Equals(progress.Status, "completed", StringComparison.OrdinalIgnoreCase)
                || string.Equals(progress.Stage, "completed", StringComparison.OrdinalIgnoreCase);
            string reason = string.IsNullOrWhiteSpace(progress.Detail)
                ? (succeeded ? "成功" : FirstNonEmpty(progress.LastFailedStage, progress.Stage, "未返回失败原因"))
                : progress.Detail.Trim();
            string account = SensitiveDataSanitizer.Redact(progress.AccountRef.Trim());
            byAccount[account] = new BackendBatchReportItem(
                account,
                succeeded ? "成功" : "失败",
                SensitiveDataSanitizer.Redact(reason),
                succeeded);
        }
        return byAccount.Values.ToList();
    }

    private static bool ResultSucceeded(JsonElement row)
    {
        foreach (string name in new[] { "ok", "success" })
        {
            if (!row.TryGetProperty(name, out JsonElement value))
                continue;
            if (value.ValueKind == JsonValueKind.True) return true;
            if (value.ValueKind == JsonValueKind.False) return false;
        }
        if (FirstText(row, "error", "reason").Length > 0)
            return false;
        string status = FirstText(row, "status", "decision").ToLowerInvariant();
        return status is "success" or "completed" or "registered" or "ready" or "active" or "imported";
    }

    private static string FirstError(JsonElement row)
    {
        string direct = FirstText(row, "error", "message", "reason", "decision_text", "decision");
        if (direct.Length > 0)
            return direct;
        foreach (string section in new[] { "phone_attempt", "password_attempt", "auth", "probe", "refresh", "response" })
        {
            if (row.TryGetProperty(section, out JsonElement nested) && nested.ValueKind == JsonValueKind.Object)
            {
                string nestedError = FirstText(nested, "error", "message", "reason", "decision_text");
                if (nestedError.Length > 0)
                    return nestedError;
            }
        }
        return "";
    }

    private static string FirstText(JsonElement element, params string[] names)
    {
        foreach (string name in names)
        {
            if (!element.TryGetProperty(name, out JsonElement value))
                continue;
            if (value.ValueKind == JsonValueKind.String)
            {
                string text = value.GetString()?.Trim() ?? "";
                if (text.Length > 0) return text;
            }
            else if (value.ValueKind == JsonValueKind.Number)
            {
                return value.GetRawText();
            }
        }
        return "";
    }

    private static int Number(JsonElement element, string name, int fallback)
        => element.TryGetProperty(name, out JsonElement value)
            && value.ValueKind == JsonValueKind.Number
            && value.TryGetInt32(out int number)
                ? Math.Max(0, number)
                : fallback;

    private static string FirstNonEmpty(params string[] values)
        => values.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value))?.Trim() ?? "";
}

public static class BackendBatchReportStore
{
    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    public static string Save(string rootDirectory, BackendBatchReport report)
    {
        string directory = Path.Combine(rootDirectory, "runtime", "task_reports");
        Directory.CreateDirectory(directory);
        string path = Path.Combine(directory, $"task_{report.FinishedAt:yyyyMMdd_HHmmss_fff}.json");
        File.WriteAllText(path, JsonSerializer.Serialize(report, SerializerOptions), new UTF8Encoding(false));
        return path;
    }
}
