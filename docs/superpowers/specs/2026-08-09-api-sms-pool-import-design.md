# API SMS Pool Import Design

## Goal

Add an `API 接码池` option to the WPF desktop application's existing `一键接码` workflow. Operators can paste pre-validated phone/SMS endpoint pairs and run the same Codex OAuth phone-verification flow used by the existing SMS providers.

## Accepted Input

Each non-empty line uses exactly this logical shape:

```text
号码---URL
```

Example:

```text
19862940168---http://sms66.vip/apisms/example-token
```

The parser splits on the first `---` sequence only so URL paths and query strings remain unchanged. It trims surrounding whitespace, normalizes the phone number to international `+` form, accepts `http://` and `https://` URLs, removes exact duplicate pairs, and reports malformed lines before starting the task.

## Desktop Flow

1. The operator selects one or more accounts and clicks `一键接码`.
2. The provider dialog includes `API 接码池` alongside the configured SMS provider choices.
3. Choosing `API 接码池` opens a multiline import dialog with validation feedback.
4. On confirmation, the desktop writes the validated entries to a temporary JSON file and starts the backend with `--phone-source phone_pool --phone-pool-file <path>`.
5. The existing mailbox selection, email OTP, phone OTP, token persistence, proxy, and account status behavior remains unchanged.
6. The temporary file is removed after the backend process exits or is cancelled.

The imported pool is scoped to the current run. It does not modify `config.json` or replace a saved SMS66/SMSBower configuration.

## Backend Changes

Add `--phone-pool-file` as an optional CLI argument. The file contains a JSON array with `phone` and `sms_api_url` fields. When the source is `phone_pool`, these entries override the configured static pool for that process only.

Extend `create_phone_pool` with an optional explicit-entry parameter. Explicit entries are converted through the existing static slot factory, preserving the current OTP send, SMS baseline, polling, reuse-count, timeout, cooldown, and state behavior.

No new SMS provider adapter is required. Imported URL pairs use the existing static SMS provider adapter.

## Validation And Security

- Reject missing phone numbers, missing separators, empty URLs, unsupported URL schemes, and invalid phone values.
- Require at least one valid pair before enabling execution.
- Allow arbitrary operator-approved HTTP/HTTPS hosts; no host allowlist is added.
- Never print the complete polling URL because its path may contain a credential. UI summaries and logs show only a redacted host-level description.
- Fail validation before launching Python so malformed input cannot partially start a batch.

## Error Handling

Parsing errors identify the input line and reason. Runtime polling failures remain isolated to the affected phone slot/account and use existing timeout and retry reporting. Existing SMS66 and SMSBower behavior is unchanged when `API 接码池` is not selected.

## Tests

- WPF parser accepts the documented single-line and multiline formats.
- WPF parser preserves URL query/path content after the first separator.
- WPF parser normalizes phone numbers, removes duplicate pairs, and rejects malformed input.
- Desktop command construction selects `phone_pool` and includes the temporary pool file.
- CLI loads explicit pool entries and gives them precedence over configured static entries for the current run.
- Static adapter polling and one-click SMS continue to work with explicit entries.
- Existing provider and phone-pool tests remain green.

