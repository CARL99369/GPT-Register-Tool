"""SMS66 API client.

SMS66 exposes a small JSON API on ``app.yuntl.cc``.  A purchased number is
long-lived, so completing/cancelling a verification must not release it; the
same number can be reused until the configured pool reuse limit is reached.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests as _requests


DEFAULT_ENDPOINT = "https://app.yuntl.cc"
BUY_PHONE_PATH = "/api/buy_phone"
GET_SMS_PATH = "/api/get_sms"
PHONE_DETAIL_PATH = "/api/get_phone_detail"
AVAILABLE_PHONES_PATH = "/api/designated_available_phones"
BUY_DESIGNATED_PHONE_PATH = "/api/buy_designated_phone"


def normalize_phone(phone: str) -> str:
    value = str(phone or "").strip()
    if not value:
        return ""
    if value.startswith("+"):
        return "+" + "".join(ch for ch in value[1:] if ch.isdigit())
    if value.startswith("00"):
        return "+" + "".join(ch for ch in value[2:] if ch.isdigit())
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"+{digits}" if digits else ""


def extract_sms_code(text: str) -> str:
    """Return the most likely verification code from SMS content."""
    value = str(text or "")
    six_digit = re.findall(r"(?<!\d)\d{6}(?!\d)", value)
    if six_digit:
        return six_digit[-1]
    candidates = re.findall(r"(?<!\d)\d{4,8}(?!\d)", value)
    return candidates[-1] if candidates else ""


@dataclass
class Sms66Activation:
    order_id: str
    phone: str
    app_id: str
    country_id: str
    acquired_at: float = field(default_factory=time.time)


@dataclass
class Sms66Client:
    api_key: str = ""
    endpoint: str = DEFAULT_ENDPOINT
    timeout: int = 15

    def _url(self, path: str) -> str:
        return f"{str(self.endpoint or DEFAULT_ENDPOINT).rstrip('/')}/{path.lstrip('/')}"

    def _json(self, method: str, path: str, params: dict) -> dict:
        request = _requests.get if method.upper() == "GET" else _requests.post
        response = request(
            self._url(path),
            params=params if method.upper() == "GET" else None,
            data=params if method.upper() != "GET" else None,
            timeout=self.timeout,
        )
        response.raise_for_status()
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"SMS66 invalid JSON response: {response.text[:200]}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"SMS66 invalid response: {str(data)[:200]}")
        return data

    @staticmethod
    def _ensure_ok(data: dict) -> dict:
        status = str(data.get("sta") or data.get("status") or "").strip().lower()
        if status not in {"ok", "success", "1", "true"}:
            raise RuntimeError(str(data.get("msg") or data.get("message") or data)[:300])
        return data

    def buy_number(
        self,
        app_id: str = "480",
        country_id: str = "1",
        duration: str = "",
    ) -> Sms66Activation:
        params = {
            "api_key": self.api_key,
            "country_id": str(country_id),
            "app_id": str(app_id),
            "buy_num": "1",
        }
        if str(duration or "").strip():
            params["duration"] = str(duration).strip()
        data = self._ensure_ok(self._json("POST", BUY_PHONE_PATH, params))
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        order_id = str(payload.get("order_id") or payload.get("orderId") or "").strip()
        phones = payload.get("phones") or payload.get("phone") or []
        if isinstance(phones, str):
            phones = [phones]
        phone = normalize_phone(phones[0] if isinstance(phones, list) and phones else "")
        if not order_id or not phone:
            raise RuntimeError(f"SMS66 buy response missing order_id/phone: {data}")
        return Sms66Activation(order_id, phone, str(app_id), str(country_id))

    def get_available_numbers(self, app_id: str = "480", limit: int = 2000, offset: int = 0) -> list[dict]:
        data = self._ensure_ok(self._json("GET", AVAILABLE_PHONES_PATH, {
            "api_key": self.api_key,
            "app_id": str(app_id),
            "limit": str(max(1, min(2000, int(limit or 2000)))),
            "offset": str(max(0, int(offset or 0))),
        }))
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        result = []
        for item in payload.get("list") or []:
            if not isinstance(item, dict):
                continue
            phone = normalize_phone(item.get("phone") or "")
            if phone:
                result.append({
                    "phone": phone,
                    "expiration_date": str(item.get("expiration_date") or "").strip(),
                })
        return result

    def buy_designated_number(self, app_id: str, phone: str) -> Sms66Activation:
        normalized = normalize_phone(phone)
        if not normalized:
            raise RuntimeError("SMS66 designated phone is empty")
        data = self._ensure_ok(self._json("POST", BUY_DESIGNATED_PHONE_PATH, {
            "api_key": self.api_key,
            "app_id": str(app_id),
            "phones": normalized.lstrip("+"),
        }))
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        order_id = str(payload.get("order_id") or payload.get("orderId") or "").strip()
        phones = payload.get("phones") or []
        if isinstance(phones, str):
            phones = [phones]
        purchased = normalize_phone(phones[0] if isinstance(phones, list) and phones else "")
        if not order_id or not purchased:
            failed = payload.get("failed_phones") or []
            raise RuntimeError(f"SMS66 designated purchase failed: {failed or data}")
        return Sms66Activation(order_id, purchased, str(app_id), "")

    def get_sms(self, app_id: str, phone: str) -> list[dict]:
        data = self._ensure_ok(self._json("GET", GET_SMS_PATH, {
            "api_key": self.api_key,
            "app_id": str(app_id),
            "phones": str(phone).lstrip("+"),
        }))
        rows = data.get("data")
        if isinstance(rows, dict):
            rows = [dict(item, phone=key) if isinstance(item, dict) else {"phone": key, "sms_content": item}
                    for key, item in rows.items()]
        return rows if isinstance(rows, list) else []

    def wait_for_code(
        self,
        app_id: str,
        phone: str,
        timeout: int = 120,
        poll_interval: int = 5,
        previous_code: str = "",
    ) -> Optional[str]:
        deadline = time.time() + max(1, int(timeout or 120))
        previous_code = str(previous_code or "").strip()
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                for item in self.get_sms(app_id, phone):
                    content = item.get("sms_content") or item.get("content") or item.get("sms") or ""
                    code = extract_sms_code(content)
                    if code and code != previous_code:
                        return code
            except Exception as exc:
                print(f"  [sms66] poll attempt {attempt} error: {exc}")
            time.sleep(min(max(1, int(poll_interval or 5)), max(1, deadline - time.time())))
        return None

    def get_phone_detail(self, app_id: str, phone: str) -> dict:
        data = self._ensure_ok(self._json("GET", PHONE_DETAIL_PATH, {
            "api_key": self.api_key,
            "app_id": str(app_id),
            "phones": str(phone).lstrip("+"),
        }))
        return data.get("data") if isinstance(data.get("data"), dict) else {}
