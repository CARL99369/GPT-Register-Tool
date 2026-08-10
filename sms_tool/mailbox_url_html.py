import hashlib
import json
import re
from dataclasses import dataclass, field
from html import unescape
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import requests


_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
_HIDDEN_TAGS = {"script", "style", "noscript", "svg", "template"}
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "br", "dd",
    "details", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "li", "main", "nav", "ol", "p", "pre", "section", "summary",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
_CONTAINER_TAGS = {"article", "details", "li", "table", "tr"}
_CONTAINER_MARKERS = re.compile(r"(?i)(mail|message|inbox|card|item|row|letter)")
_SUBJECT_MARKERS = re.compile(r"(?i)(subject|title|topic|主题|标题)")
_DATE_MARKERS = re.compile(r"(?i)(date|time|received|sent|日期|时间)")
_OTP_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_OTP_TOPIC_RE = re.compile(
    r"(?i)(chatgpt|openai|verification\s+code|login\s+code|verify|验证码|驗證碼)"
)
_OTP_SUBJECT_RE = re.compile(r"(?i)(verification\s+code|login\s+code|验证码|驗證碼)")
_EMAIL_RE = re.compile(r"(?i)[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}")
_DATE_RE = re.compile(
    r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})"
    r"(?:[T\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
    r"(?:\s*(Z|[+-]\d{2}:?\d{2}))?"
)
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_SRCDOC_DEPTH = 3
_ARKASM_HOST = "icloud.arkasm.cn"
_FLYSMS_HOST = "flysms.xyz"
_FLYSMS_PICKUP_PATH = "/icloud/pickup"
_FLYSMS_TOKEN_RE = re.compile(r"^tok_[A-Za-z0-9_-]+$")


class UrlHtmlMailboxError(RuntimeError):
    pass


def redact_inbox_url(value):
    try:
        parsed = urlsplit(str(value or ""))
        host = parsed.hostname or "invalid-host"
        port = f":{parsed.port}" if parsed.port else ""
        scheme = parsed.scheme.lower() or "https"
    except ValueError:
        host = "invalid-host"
        port = ""
        scheme = "https"
    return f"{scheme}://{host}{port}/<redacted>"


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: "_Node | None" = None
    children: list = field(default_factory=list)


class _TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(
            str(tag or "").lower(),
            {str(key or "").lower(): str(value or "") for key, value in attrs},
            self.stack[-1],
        )
        self.stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = _Node(
            str(tag or "").lower(),
            {str(key or "").lower(): str(value or "") for key, value in attrs},
            self.stack[-1],
        )
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        lowered = str(tag or "").lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == lowered:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(str(data))


def _parse_html_tree(html):
    parser = _TreeParser()
    parser.feed(str(html or ""))
    parser.close()
    return parser.root


def _walk(node):
    for child in node.children:
        if not isinstance(child, _Node):
            continue
        yield child
        yield from _walk(child)


def _text_parts(node, output, srcdoc_depth=0):
    if node.tag in _HIDDEN_TAGS:
        return

    srcdoc = str(node.attrs.get("srcdoc") or "")
    if node.tag == "iframe" and srcdoc and srcdoc_depth < MAX_SRCDOC_DEPTH:
        output.append("\n")
        embedded_root = _parse_html_tree(unescape(srcdoc))
        _text_parts(embedded_root, output, srcdoc_depth + 1)
        output.append("\n")
        return

    is_block = node.tag in _BLOCK_TAGS
    if is_block:
        output.append("\n")
    for child in node.children:
        if isinstance(child, _Node):
            _text_parts(child, output, srcdoc_depth)
        else:
            output.append(child)
    if is_block:
        output.append("\n")


def _visible_text(node):
    parts = []
    _text_parts(node, parts)
    lines = []
    for line in "".join(parts).splitlines():
        normalized = re.sub(r"[\t\r\f\v ]+", " ", line).strip()
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def _attrs_text(node):
    return " ".join(
        str(node.attrs.get(key) or "")
        for key in ("class", "id", "name", "role", "data-field", "itemprop")
    )


def _semantic_message_containers(root):
    containers = []
    for node in _walk(root):
        text = _visible_text(node)
        if not _OTP_RE.search(text):
            continue
        if node.tag in _CONTAINER_TAGS or _CONTAINER_MARKERS.search(_attrs_text(node)):
            containers.append(node)
    return containers


def _first_semantic_text(node, marker):
    for descendant in _walk(node):
        if marker.search(_attrs_text(descendant)):
            text = _visible_text(descendant)
            if text:
                return text.splitlines()[0][:300]
    return ""


def _message_subject(node, text):
    subject = _first_semantic_text(node, _SUBJECT_MARKERS)
    if subject:
        return subject
    for descendant in _walk(node):
        if descendant.tag in {"summary", "h1", "h2", "h3", "h4"}:
            candidate = _visible_text(descendant).strip()
            if candidate and _OTP_TOPIC_RE.search(candidate):
                return candidate.splitlines()[0][:300]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if _OTP_SUBJECT_RE.search(line):
            return line[:300]
    for line in lines:
        if _OTP_TOPIC_RE.search(line):
            return line[:300]
    return (lines[0] if lines else "URL mailbox message")[:300]


def _extract_labeled_email(text, labels):
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?i)(?:{label_pattern})\s*[:：]\s*([^\n]+)", text)
    if not match:
        return ""
    email = _EMAIL_RE.search(match.group(1))
    return email.group(0).lower() if email else match.group(1).strip()[:300]


def _message_sender(text):
    return _extract_labeled_email(text, ("from", "sender", "发件人", "寄件人"))


def _message_recipient(text):
    return _extract_labeled_email(text, ("to", "recipient", "收件人"))


def _message_date(node, text):
    date_text = _first_semantic_text(node, _DATE_MARKERS)
    match = _DATE_RE.search(date_text) if date_text else None
    if not match:
        match = _DATE_RE.search(text)
    if not match:
        return ""
    year, month, day, hour, minute, second, timezone = match.groups()
    value = (
        f"{int(year):04d}-{int(month):02d}-{int(day):02d}T"
        f"{int(hour or 0):02d}:{int(minute or 0):02d}:{int(second or 0):02d}"
    )
    if timezone:
        value += "+00:00" if timezone == "Z" else timezone
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def _otp_contexts(text):
    contexts = []
    for match in _OTP_RE.finditer(text):
        start = max(0, match.start() - 140)
        end = min(len(text), match.end() + 140)
        context = re.sub(r"\s+", " ", text[start:end]).strip().lower()
        if context and context not in contexts:
            contexts.append(context)
    return contexts


def _message_id(subject, sender, recipient, received, body):
    contexts = _otp_contexts(body)
    stable_body = "\n".join(contexts) if contexts else re.sub(r"\s+", " ", body)[:500]
    signature = "\n".join((subject, sender, recipient, received, stable_body)).lower()
    return "url-html:" + hashlib.sha256(signature.encode("utf-8")).hexdigest()


def _message_from_text(text, mailbox_email, node=None):
    body = str(text or "").strip()
    if not body or not _OTP_RE.search(body):
        return None
    subject = _message_subject(node, body) if node is not None else _message_subject(_Node("fallback"), body)
    sender = _message_sender(body)
    recipient = _message_recipient(body)
    received = _message_date(node, body) if node is not None else _message_date(_Node("fallback"), body)
    recipients = []
    if recipient:
        recipients = [{"emailAddress": {"address": recipient}}]
    return {
        "id": _message_id(subject, sender, recipient, received, body),
        "subject": subject,
        "receivedDateTime": received,
        "from": sender,
        "bodyPreview": body[:1000],
        "body": {"content": body},
        "toRecipients": recipients,
    }


def _fallback_context_messages(text, mailbox_email):
    messages = []
    for match in _OTP_RE.finditer(text):
        start = max(0, match.start() - 300)
        end = min(len(text), match.end() + 300)
        message = _message_from_text(text[start:end], mailbox_email)
        if message:
            messages.append(message)
    return messages


def _plus_alias_base(value):
    email = str(value or "").strip().lower()
    if "@" not in email:
        return ""
    local, domain = email.rsplit("@", 1)
    local = local.split("+", 1)[0]
    if not local or not domain:
        return ""
    return f"{local}@{domain}"


def parse_url_html_messages(html, mailbox_email, limit=25):
    root = _parse_html_tree(str(html or ""))
    messages = []
    for node in _semantic_message_containers(root):
        message = _message_from_text(_visible_text(node), mailbox_email, node=node)
        if message:
            messages.append(message)
    if not messages:
        messages = _fallback_context_messages(_visible_text(root), mailbox_email)

    unique = []
    seen = set()
    for message in messages:
        if message["id"] in seen:
            continue
        seen.add(message["id"])
        unique.append(message)
        if len(unique) >= max(1, int(limit or 25)):
            break
    return unique


def _parse_generic_url_payload(text, mailbox_email, safe_url, limit=25):
    try:
        payload = json.loads(str(text or ""))
    except (TypeError, ValueError):
        return parse_url_html_messages(text, mailbox_email, limit=limit)

    if isinstance(payload, dict):
        status_code = str(payload.get("code") or "").strip().lower()
        if payload.get("success") is True and re.fullmatch(r"\d{6}", status_code):
            subject = str(payload.get("subject") or "").strip()[:300]
            message_text = str(payload.get("message") or "").strip()
            body = "\n".join(
                value
                for value in (
                    subject,
                    message_text,
                    f"ChatGPT verification code: {status_code}",
                )
                if value
            )
            message = _message_from_text(body, mailbox_email)
            if message:
                if subject:
                    message["subject"] = subject
                received_at = str(
                    payload.get("received_at")
                    or payload.get("receivedDateTime")
                    or ""
                ).strip()
                if received_at:
                    message["receivedDateTime"] = received_at
                message_id = str(
                    payload.get("message_id") or payload.get("id") or ""
                ).strip()
                if message_id:
                    message["id"] = f"url-json:{message_id}"
                recipient = str(payload.get("email") or mailbox_email or "").strip()
                mailbox_recipient = str(mailbox_email or "").strip()
                if (
                    recipient
                    and mailbox_recipient
                    and _plus_alias_base(recipient) == _plus_alias_base(mailbox_recipient)
                ):
                    recipient = mailbox_recipient
                if recipient:
                    message["toRecipients"] = [
                        {"emailAddress": {"address": recipient.lower()}}
                    ]
            return [message] if message else []
        if payload.get("retryable") is True or status_code == "no_code":
            return []
    raise UrlHtmlMailboxError(
        f"URL mailbox returned unsupported JSON for {safe_url}"
    )


def _request_text(
    url,
    safe_url,
    proxy,
    http_get,
    accept,
    content_types,
    request_headers=None,
):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"Accept": accept}
    if request_headers:
        headers.update(request_headers)
    try:
        response = http_get(
            url,
            headers=headers,
            proxies=proxies,
            timeout=(10, 20),
            stream=True,
            allow_redirects=True,
        )
    except Exception as exc:
        raise UrlHtmlMailboxError(
            f"URL mailbox request failed for {safe_url}: {type(exc).__name__}"
        ) from exc

    if not 200 <= int(response.status_code or 0) < 300:
        response.close()
        raise UrlHtmlMailboxError(
            f"URL mailbox returned HTTP {response.status_code} for {safe_url}"
        )
    content_type = str(response.headers.get("Content-Type") or "").lower()
    if not any(kind in content_type for kind in content_types):
        response.close()
        raise UrlHtmlMailboxError(
            f"URL mailbox returned unsupported content for {safe_url}"
        )

    chunks = []
    size = 0
    try:
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_HTML_BYTES:
                raise UrlHtmlMailboxError(
                    f"URL mailbox response exceeds 2 MiB for {safe_url}"
                )
            chunks.append(chunk)
        payload = b"".join(chunks)
    except UrlHtmlMailboxError:
        raise
    except Exception as exc:
        raise UrlHtmlMailboxError(
            f"URL mailbox read failed for {safe_url}: {type(exc).__name__}"
        ) from exc
    finally:
        response.close()

    encoding = str(getattr(response, "encoding", "") or "utf-8")
    return payload.decode(encoding, errors="replace")


def _arkasm_api_base(url):
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return ""
    if (parsed.hostname or "").lower() != _ARKASM_HOST:
        return ""
    match = re.fullmatch(r"/share/([^/]+)/?", parsed.path or "")
    if not match:
        return ""
    token = match.group(1).strip()
    if not token:
        return ""
    return (
        f"{parsed.scheme.lower()}://{parsed.netloc}/api/public/share/"
        f"{quote(token, safe='')}"
    )


def _flysms_credentials(url, mailbox_email, safe_url):
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return None
    if (
        (parsed.hostname or "").lower() != _FLYSMS_HOST
        or (parsed.path or "").rstrip("/") != _FLYSMS_PICKUP_PATH
    ):
        return None

    fragment = parse_qs(parsed.fragment, keep_blank_values=True)
    emails = fragment.get("email", [])
    keys = fragment.get("key", [])
    if len(emails) != 1 or len(keys) != 1:
        raise UrlHtmlMailboxError(
            f"FlySMS pickup URL has invalid credentials for {safe_url}"
        )
    email = str(emails[0] or "").strip().lower()
    expected_email = str(mailbox_email or "").strip().lower()
    token = str(keys[0] or "").strip()
    if (
        not email
        or email != expected_email
        or not token
        or len(token) > 512
        or not _FLYSMS_TOKEN_RE.fullmatch(token)
    ):
        raise UrlHtmlMailboxError(
            f"FlySMS pickup URL has invalid credentials for {safe_url}"
        )
    return parsed, email, token


def _flysms_message(record):
    item = record if isinstance(record, dict) else {}
    mailbox_name = str(item.get("mailbox") or "INBOX").strip() or "INBOX"
    uid = str(item.get("uid") or "").strip()
    if not uid:
        return None
    recipient_text = str(item.get("to") or "")
    recipients = [
        {"emailAddress": {"address": address.lower()}}
        for address in _EMAIL_RE.findall(recipient_text)
    ]
    body = str(item.get("text") or item.get("preview") or "")
    if not body and item.get("html"):
        body = _visible_text(_parse_html_tree(str(item.get("html") or "")))
    return {
        "id": f"flysms:{mailbox_name}:{uid}",
        "subject": str(item.get("subject") or "")[:300],
        "receivedDateTime": str(item.get("date") or ""),
        "from": str(item.get("from") or "")[:500],
        "bodyPreview": str(item.get("preview") or body)[:1000],
        "body": {"content": body},
        "toRecipients": recipients,
    }


def _parse_flysms_json(text, mailbox_email, safe_url, limit):
    try:
        payload = json.loads(str(text or ""))
    except (TypeError, ValueError) as exc:
        raise UrlHtmlMailboxError(
            f"FlySMS returned invalid JSON for {safe_url}"
        ) from exc
    if not isinstance(payload, dict):
        raise UrlHtmlMailboxError(
            f"FlySMS returned invalid data for {safe_url}"
        )
    response_email = str(payload.get("email") or "").strip().lower()
    expected_email = str(mailbox_email or "").strip().lower()
    records = payload.get("messages")
    if response_email != expected_email or not isinstance(records, list):
        raise UrlHtmlMailboxError(
            f"FlySMS returned invalid data for {safe_url}"
        )
    messages = []
    for record in records[:max(1, int(limit or 25))]:
        message = _flysms_message(record)
        if message:
            messages.append(message)
    return messages


def _fetch_flysms_messages(
    mailbox,
    credentials,
    limit,
    proxy,
    http_get,
    safe_url,
):
    parsed, email, token = credentials
    normalized_limit = max(1, min(int(limit or 25), 100))
    api_url = (
        f"{parsed.scheme.lower()}://{parsed.netloc}"
        f"/icloud/api/pickup/messages?{urlencode({'limit': normalized_limit})}"
    )
    text = _request_text(
        api_url,
        safe_url,
        proxy,
        http_get,
        "application/json",
        ("application/json",),
        request_headers={
            "Authorization": f"Bearer {token}",
            "X-Mailbox-Email": email,
        },
    )
    return _parse_flysms_json(text, mailbox.email, safe_url, normalized_limit)


def _arkasm_message(record):
    item = record if isinstance(record, dict) else {}
    message_id = str(item.get("id") or "").strip()
    recipient_text = str(item.get("to") or "")
    recipients = [
        {"emailAddress": {"address": address.lower()}}
        for address in _EMAIL_RE.findall(recipient_text)
    ]
    body = str(item.get("body") or "")
    if "html" in str(item.get("content_type") or "").lower():
        body = _visible_text(_parse_html_tree(body))
    return {
        "id": f"arkasm:{message_id}",
        "subject": str(item.get("subject") or "")[:300],
        "receivedDateTime": str(item.get("date") or ""),
        "from": str(item.get("from") or "")[:500],
        "bodyPreview": str(item.get("preview") or "")[:1000],
        "body": {"content": body},
        "toRecipients": recipients,
    }


def _parse_arkasm_json(text, safe_url, expected_key):
    try:
        payload = json.loads(str(text or ""))
    except (TypeError, ValueError) as exc:
        raise UrlHtmlMailboxError(
            f"URL mailbox returned invalid JSON for {safe_url}"
        ) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if payload.get("success") is not True or not isinstance(data, dict):
        raise UrlHtmlMailboxError(
            f"URL mailbox API rejected the request for {safe_url}"
        )
    value = data.get(expected_key)
    if expected_key == "messages" and not isinstance(value, list):
        raise UrlHtmlMailboxError(
            f"URL mailbox API returned invalid data for {safe_url}"
        )
    return data


def _fetch_arkasm_messages(mailbox, api_base, limit, proxy, http_get, safe_url):
    normalized_limit = max(1, int(limit or 25))
    inbox_url = f"{api_base}/inbox?{urlencode({'limit': normalized_limit, 'days': 7})}"
    inbox_text = _request_text(
        inbox_url,
        safe_url,
        proxy,
        http_get,
        "application/json",
        ("application/json",),
    )
    inbox_data = _parse_arkasm_json(inbox_text, safe_url, "messages")
    messages = []
    for summary in inbox_data["messages"][:normalized_limit]:
        if not isinstance(summary, dict):
            continue
        message_id = str(summary.get("id") or "").strip()
        if not message_id:
            continue
        query = {"uid": message_id}
        folder = str(summary.get("folder") or "").strip()
        if folder:
            query["folder"] = folder
        detail_url = f"{api_base}/message?{urlencode(query)}"
        detail_text = _request_text(
            detail_url,
            safe_url,
            proxy,
            http_get,
            "application/json",
            ("application/json",),
        )
        detail = _parse_arkasm_json(detail_text, safe_url, "id")
        messages.append(_arkasm_message({**summary, **detail}))
    return messages


def fetch_url_html_messages(mailbox, limit=25, proxy="", http_get=requests.get):
    url = str(getattr(mailbox, "inbox_url", "") or "").strip()
    safe_url = redact_inbox_url(url)
    flysms_credentials = _flysms_credentials(url, mailbox.email, safe_url)
    if flysms_credentials:
        return _fetch_flysms_messages(
            mailbox,
            flysms_credentials,
            limit,
            proxy,
            http_get,
            safe_url,
        )
    arkasm_api = _arkasm_api_base(url)
    if arkasm_api:
        return _fetch_arkasm_messages(
            mailbox,
            arkasm_api,
            limit,
            proxy,
            http_get,
            safe_url,
        )

    html = _request_text(
        url,
        safe_url,
        proxy,
        http_get,
        "text/html,text/plain;q=0.9,application/json;q=0.9",
        ("text/html", "text/plain", "application/xhtml+xml", "application/json"),
    )
    return _parse_generic_url_payload(
        html,
        mailbox.email,
        safe_url,
        limit=limit,
    )
