namespace SmsWorkbench
{
    public static class OAuthOperationState
    {
        public static string Display(string ok, string error)
        {
            if (string.Equals((ok ?? "").Trim(), "false", System.StringComparison.OrdinalIgnoreCase)
                || (!string.IsNullOrWhiteSpace(error)
                    && !string.Equals((ok ?? "").Trim(), "true", System.StringComparison.OrdinalIgnoreCase)))
            {
                return "RT获取失败";
            }

            return string.Equals((ok ?? "").Trim(), "true", System.StringComparison.OrdinalIgnoreCase)
                ? "RT获取成功"
                : "";
        }
    }
}
