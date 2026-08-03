import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser


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


def _text_parts(node, output):
    if node.tag in _HIDDEN_TAGS:
        return
    is_block = node.tag in _BLOCK_TAGS
    if is_block:
        output.append("\n")
    for child in node.children:
        if isinstance(child, _Node):
            _text_parts(child, output)
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
