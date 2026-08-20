using System;
using System.Collections.Generic;
using System.Linq;

namespace SmsWorkbench
{
    internal static class MailboxUnusedExport
    {
        internal static bool IsRegisteredStatus(string status, string payPalStatus = "")
        {
            string value = ((status ?? "") + " " + (payPalStatus ?? "")).Trim();
            if (value.Length == 0) return false;
            return value.Contains("已注册", StringComparison.Ordinal)
                || value.Contains("PayPal", StringComparison.OrdinalIgnoreCase)
                || value.Contains("待支付", StringComparison.Ordinal)
                || value.Contains("支付完成", StringComparison.Ordinal)
                || value.Contains("Payment completed", StringComparison.OrdinalIgnoreCase)
                || value.Contains("已导入", StringComparison.Ordinal)
                || value.Contains("PM已创建", StringComparison.Ordinal)
                || value.Equals("completed", StringComparison.OrdinalIgnoreCase);
        }

        internal static bool IsSessionLikeAccountType(string accountType)
        {
            string value = accountType ?? "";
            return value.Contains("Session", StringComparison.OrdinalIgnoreCase)
                || value.Contains("SQLite", StringComparison.OrdinalIgnoreCase);
        }

        internal static bool IsUnusedMailboxRow(
            string accountType,
            string status,
            string payPalStatus,
            bool hasAccessToken,
            bool hasMailboxCredential)
        {
            if (!hasMailboxCredential) return false;
            if (hasAccessToken) return false;
            if (IsSessionLikeAccountType(accountType)) return false;
            if (IsRegisteredStatus(status, payPalStatus)) return false;
            return true;
        }

        internal static string ResolveExportLine(string mailboxLine, string rawLine, Func<string, bool> isUsableLine = null)
        {
            foreach (string candidate in new[] { mailboxLine, rawLine })
            {
                string value = NormalizeLine(candidate);
                if (value.Length == 0) continue;
                if (isUsableLine == null || isUsableLine(value)) return value;
            }
            return "";
        }

        internal static IReadOnlyList<string> CollectExportLines(
            IEnumerable<(string Email, string Line)> rows,
            out int skipped)
        {
            skipped = 0;
            var lines = new List<string>();
            var seenLines = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var seenEmails = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach ((string email, string line) in rows ?? Enumerable.Empty<(string, string)>())
            {
                string exportLine = NormalizeLine(line);
                if (exportLine.Length == 0)
                {
                    skipped++;
                    continue;
                }

                string emailKey = MailboxPoolFileStore.NormalizeEmailKey(email);
                if (emailKey.Length > 0 && !seenEmails.Add(emailKey))
                {
                    skipped++;
                    continue;
                }
                if (!seenLines.Add(exportLine))
                {
                    skipped++;
                    continue;
                }
                lines.Add(exportLine);
            }

            return lines;
        }

        private static string NormalizeLine(string line)
        {
            return (line ?? "").Trim().TrimStart('﻿');
        }
    }
}
