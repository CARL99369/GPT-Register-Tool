using System;
using System.Linq;
using System.Text.RegularExpressions;

namespace SmsWorkbench
{
    internal readonly record struct MailboxLineInfo(
        string Email,
        string Provider,
        string CommandArgument,
        string NormalizedLine);

    internal static class MailboxLineParser
    {
        private static readonly string[] TripleDelimiter = { "---" };
        private static readonly string[] QuadDelimiter = { "----" };
        private static readonly string[] MailboxDelimiters = { "----", "---" };
        private static readonly Regex UrlMailboxDelimiter = new(
            @"-{4,}(?=https?://)",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

        internal static bool TryParse(string line, out MailboxLineInfo info)
        {
            info = default;
            string value = (line ?? "").Trim().TrimStart('\ufeff');
            if (value.Length == 0 || value.StartsWith('#')) return false;

            Match urlDelimiter = UrlMailboxDelimiter.Match(value);
            if (urlDelimiter.Success && urlDelimiter.Index > 0)
            {
                string email = value.Substring(0, urlDelimiter.Index).Trim();
                string remainder = value.Substring(urlDelimiter.Index + urlDelimiter.Length).Trim();
                if (LooksLikeEmail(email)
                    && Uri.TryCreate(remainder, UriKind.Absolute, out Uri? uri)
                    && uri.Host.Length > 0
                    && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps))
                {
                    info = new MailboxLineInfo(email, "url_html", "--chatai-mailbox-file", value);
                    return true;
                }
            }

            if (value.StartsWith("cfworker://", StringComparison.OrdinalIgnoreCase)
                || value.EndsWith("@edu.liziai.cloud", StringComparison.OrdinalIgnoreCase)
                || value.EndsWith("@liziai.cloud", StringComparison.OrdinalIgnoreCase))
            {
                string email = value.StartsWith("cfworker://", StringComparison.OrdinalIgnoreCase)
                    ? value.Substring("cfworker://".Length).Trim()
                    : value;
                info = new MailboxLineInfo(email, "cfworker", "--mailbox-file", value);
                return true;
            }

            if (value.StartsWith("remail://", StringComparison.OrdinalIgnoreCase))
            {
                string payload = value.Substring("remail://".Length);
                string[] parts = payload.Split(TripleDelimiter, 4, StringSplitOptions.None);
                if (parts.Length < 3) return false;
                info = new MailboxLineInfo(parts[0].Trim(), "remail", "--mailbox-file", value);
                return true;
            }

            if (value.StartsWith("gmail://", StringComparison.OrdinalIgnoreCase))
            {
                string payload = value.Substring("gmail://".Length);
                string email = payload.Split(MailboxDelimiters, StringSplitOptions.None)[0].Trim();
                info = new MailboxLineInfo(email, "gmail", "--mailbox-file", value);
                return true;
            }

            if (value.Contains("----")
                && value.Split(QuadDelimiter, StringSplitOptions.None).Length >= 4)
            {
                string email = value.Split(QuadDelimiter, StringSplitOptions.None)[0].Trim();
                info = new MailboxLineInfo(email, "chatai", "--chatai-mailbox-file", value);
                return true;
            }

            if (value.Contains("---")
                && value.Split(TripleDelimiter, StringSplitOptions.None).Length >= 3)
            {
                string email = value.Split(TripleDelimiter, StringSplitOptions.None)[0].Trim();
                info = new MailboxLineInfo(email, "graph", "--mailbox-file", value);
                return true;
            }

            return false;
        }

        private static bool LooksLikeEmail(string value)
        {
            int at = value.IndexOf('@');
            return at > 0
                && at < value.Length - 3
                && value.IndexOf('.', at) > at + 1
                && !value.Any(char.IsWhiteSpace);
        }
    }
}
