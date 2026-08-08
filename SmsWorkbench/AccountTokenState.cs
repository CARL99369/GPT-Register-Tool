using System;
using System.Collections.Generic;

namespace SmsWorkbench
{
    public static class AccountExportState
    {
        public static Dictionary<string, object> SelectSource(Dictionary<string, object> data)
        {
            if (data == null) return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);

            if (HasText(data, "access_token"))
            {
                return data;
            }

            if (data.TryGetValue("auth_session", out object value)
                && value is Dictionary<string, object> authSession
                && authSession.Count > 0)
            {
                return authSession;
            }

            return data;
        }

        private static bool HasText(Dictionary<string, object> data, string key)
        {
            return data.TryGetValue(key, out object value)
                && value is string text
                && !string.IsNullOrWhiteSpace(text);
        }
    }

    public static class RefreshTokenState
    {
        public static string Resolve(string storedStatus, params string[] oauthRefreshTokens)
        {
            foreach (string token in oauthRefreshTokens ?? Array.Empty<string>())
            {
                if (!string.IsNullOrWhiteSpace(token)) return "oauth_present";
            }

            return (storedStatus ?? "").Trim();
        }
    }
}
