import base64
import argparse
import hashlib
import json
import secrets
import time
import urllib.parse
from datetime import timezone, datetime

from curl_cffi import requests as curl_requests

from .config import CFG
from .codex_phone import complete_phone_verification
from .codex_sentinel import attach_sentinel, import_cached_auth_cookies, import_cookie_header, load_cached_sentinel, with_sentinel
from .auth_headers import auth_impersonate, openai_auth_headers_lower, select_auth_fingerprint
from .http_client import request_with_retry
from .mailbox import MailboxAccount, MailboxTokenExpiredError, _poll_email_otp, mailbox_has_inbox_credentials
from . import mailbox_gmail
from .storage import upsert_account
from .totp import generate_totp


AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPE = "openid profile email offline_access api.connectors.read api.connectors.invoke"
ORIGINATOR = "codex_cli_rs"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/110.0.0.0 Safari/537.36"
LOGIN_EMAIL_OTP_SUBJECT_KEYWORD = "login code|登录代码|验证码|临时登录代码|临时验证码|ログインコード|認証コード|一時的な認証コード|登入代碼|驗證碼|로그인 코드|인증 코드"


def refresh_codex_oauth_session(
    data,
    json_path="",
    proxy=None,
    timeout=180,
    force_email_otp_login=False,
    phone_pool=None,
    phone_probe_only=False,
    persist=True,
):
    if _is_terminal_account_data(data):
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": "account_deactivated",
            "terminal": True,
            "skipped": True,
        }
    result = collect_codex_oauth_tokens(
        data=data,
        proxy=proxy,
        timeout=timeout,
        force_email_otp_login=force_email_otp_login,
        phone_pool=phone_pool,
        phone_probe_only=phone_probe_only,
    )
    if not result.get("ok"):
        return result
    if not persist:
        return result
    return _save_oauth_tokens(
        data,
        json_path,
        result["tokens"],
        str(data.get("email") or "").strip().lower(),
        "codex_oauth_pkce",
        result=result,
    )


def collect_codex_oauth_tokens(
    data,
    session=None,
    proxy=None,
    timeout=180,
    force_email_otp_login=False,
    phone_pool=None,
    phone_probe_only=False,
):
    select_auth_fingerprint(rotate=True)
    email = str(data.get("email") or "").strip().lower()
    if not email:
        return {"ok": False, "mode": "codex_oauth_pkce", "error": "missing_email"}
    oauth = _new_oauth_request()
    if session is None:
        device_id = str(data.get("device_id") or "").strip() or secrets.token_hex(16)
        data["device_id"] = device_id
        sentinel = _ensure_oauth_sentinel(device_id=device_id, proxy=proxy)
        if not sentinel:
            return {
                "ok": False,
                "mode": "codex_oauth_pkce",
                "error": "oauth_sentinel_extract_failed",
            }
        session = curl_requests.Session()
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}
        import_cookie_header(session, data.get("cookie_header", ""), "chatgpt.com")
        import_cached_auth_cookies(session)
    elif proxy:
        session.proxies = {"http": proxy, "https": proxy}

    try:
        _, current_url = _follow_redirects(session, oauth["auth_url"], proxy=proxy)
        if _has_callback_code(current_url):
            tokens = _exchange_callback(current_url, oauth, proxy=proxy)
            return {"ok": True, "mode": "codex_oauth_pkce", "tokens": tokens}

        result = _login_and_exchange(
            session=session,
            oauth=oauth,
            email=email,
            data=data,
            current_url=current_url,
            proxy=proxy,
            timeout=timeout,
            force_email_otp_login=force_email_otp_login,
            phone_pool=phone_pool,
            phone_probe_only=phone_probe_only,
        )
        if not result.get("ok"):
            return result
        result.setdefault("mode", "codex_oauth_pkce")
        return result
    except Exception as exc:
        return {"ok": False, "mode": "codex_oauth_pkce", "error": str(exc)}


def _ensure_oauth_sentinel(device_id, proxy=None):
    from .sentinel_tokens import (
        _extract_sentinel_http,
        _extract_sentinel_quickjs,
        _get_cached_sentinel,
        _sentinel_device_id,
    )

    did = str(device_id or "").strip()
    cached = _get_cached_sentinel()
    if cached and _sentinel_device_id(cached) == did:
        return cached

    print("[*] Preparing device-bound Sentinel for Codex OAuth...")
    for extractor in (_extract_sentinel_quickjs, _extract_sentinel_http):
        sentinel = extractor(proxy=proxy, persist=True, device_id=did)
        if sentinel and _sentinel_device_id(sentinel) == did:
            return sentinel
    return {}


def _login_and_exchange(
    session,
    oauth,
    email,
    data,
    current_url,
    proxy=None,
    timeout=180,
    force_email_otp_login=False,
    phone_pool=None,
    phone_probe_only=False,
):
    max_restarts = 2
    for restart_attempt in range(max_restarts + 1):
        did = (
            secrets.token_hex(16)
            if restart_attempt > 0
            else str(data.get("device_id") or "").strip() or _cookie_value(session, "oai-did") or secrets.token_hex(16)
        )
        try:
            session.cookies.set("oai-did", did, domain="auth.openai.com", path="/")
        except Exception:
            pass
        sentinel = load_cached_sentinel()
        headers = _oai_headers(did, {"Referer": current_url or AUTH_URL, "content-type": "application/json"})
        attach_sentinel(headers, sentinel)
        otp_issued_after_unix = int(time.time())
        start_resp = session.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers=headers,
            json={
                "username": {"value": email, "kind": "email"},
                "screen_hint": "login_or_signup",
            },
            timeout=30,
            impersonate=auth_impersonate(),
            allow_redirects=False,
        )
        if start_resp.status_code != 200:
            return {
                "ok": False,
                "mode": "codex_oauth_pkce",
                "error": f"authorize_continue_failed:{start_resp.status_code}",
                "body": start_resp.text[:300],
            }
        next_url = _next_url(start_resp)
        _, current_url = _follow_redirects(session, next_url, proxy=proxy)
        if _has_callback_code(current_url):
            return {"ok": True, "tokens": _exchange_callback(current_url, oauth, proxy=proxy)}

        result = _run_protocol_login_stages(
            session=session,
            oauth=oauth,
            email=email,
            data=data,
            did=did,
            current_url=current_url,
            proxy=proxy,
            timeout=timeout,
            force_email_otp_login=force_email_otp_login,
            phone_pool=phone_pool,
            phone_probe_only=phone_probe_only,
            initial_otp_requested=True,
            otp_issued_after_unix=otp_issued_after_unix,
        )
        if not result.get("needs_session_restart") or restart_attempt >= max_restarts:
            return result
        print(f"[*] Session state invalid, restarting auth flow (attempt {restart_attempt + 1}/{max_restarts})")
        _clear_auth_session_cookies(session)
        try:
            oauth = _new_oauth_request()
            _, current_url = _follow_redirects(session, oauth["auth_url"], proxy=proxy)
        except Exception as exc:
            return {
                "ok": False,
                "mode": "codex_oauth_pkce",
                "error": f"oauth_session_restart_failed:{exc}",
                "needs_session_restart": True,
            }
        if _has_callback_code(current_url):
            return {"ok": True, "tokens": _exchange_callback(current_url, oauth, proxy=proxy)}
    return result


def _run_protocol_login_stages(
    session,
    oauth,
    email,
    data,
    did,
    current_url,
    proxy=None,
    timeout=180,
    force_email_otp_login=False,
    phone_pool=None,
    phone_probe_only=False,
    initial_otp_requested=False,
    otp_issued_after_unix=None,
):
    stage = _detect_protocol_stage(current_url)
    data.setdefault("codex_oauth_protocol", {})
    data["codex_oauth_protocol"]["stage"] = stage
    print(f"[*] Codex OAuth protocol stage: {stage}")
    account_mfa_configured = bool(_account_password_from_data(data) and _totp_secret_from_data(data))
    allow_takeover = bool(
        force_email_otp_login
        or (_allow_passwordless_takeover() and not account_mfa_configured)
    )

    if _codex_oauth_protocol_ready_stage(stage):
        final = _finish_authorization(session, oauth, did, current_url, proxy=proxy, phone_pool=phone_pool, phone_probe_only=phone_probe_only)
        if final.get("ok"):
            final.setdefault("protocol_stage", stage)
        return final

    if force_email_otp_login and not account_mfa_configured and stage not in {"email_otp", "callback", "consent", "add_phone", "totp"}:
        print(f"[*] force_email_otp_login: overriding stage '{stage}' -> 'email_otp'")
        stage = "email_otp"
    elif stage in {"email", "unknown"}:
        stage = "email_otp" if allow_takeover else stage

    if stage == "email_otp":
        email_otp_result = _passwordless_login_and_exchange(
            session=session,
            oauth=oauth,
            data=data,
            did=did,
            current_url=current_url,
            proxy=proxy,
            timeout=timeout,
            reason="email_otp_required" if _needs_email_otp(current_url) else "email_otp_forced",
            phone_pool=phone_pool,
            phone_probe_only=phone_probe_only,
            initial_otp_requested=initial_otp_requested,
            otp_issued_after_unix=otp_issued_after_unix,
        )
        if email_otp_result.get("ok"):
            email_otp_result.setdefault("protocol_stage", "email_otp")
            return email_otp_result
        email_otp_result.setdefault("protocol_stage", "email_otp")
        email_otp_result.setdefault("fallback_from", "email_otp_forced" if force_email_otp_login else "email_otp_required")
        return email_otp_result
    elif stage == "password":
        password_result = _password_login_and_exchange(
            session=session,
            oauth=oauth,
            data=data,
            did=did,
            current_url=current_url,
            proxy=proxy,
            timeout=timeout,
            phone_pool=phone_pool,
            phone_probe_only=phone_probe_only,
        )
        if password_result.get("ok"):
            password_result.setdefault("protocol_stage", "password")
            return password_result
        if not allow_takeover:
            return password_result
        email_otp_result = _passwordless_login_and_exchange(
            session=session,
            oauth=oauth,
            data=data,
            did=did,
            current_url=current_url,
            proxy=proxy,
            timeout=timeout,
            reason="password_login_failed",
            phone_pool=phone_pool,
            phone_probe_only=phone_probe_only,
        )
        if email_otp_result.get("ok"):
            email_otp_result.setdefault("protocol_stage", "email_otp")
            return email_otp_result
        email_otp_result.setdefault("protocol_stage", "email_otp")
        email_otp_result.setdefault("fallback_from", "password_login_failed")
        email_otp_result["password_attempt"] = password_result
        return email_otp_result
    elif stage == "totp":
        totp_result = _complete_totp_and_finish(
            session=session,
            oauth=oauth,
            data=data,
            did=did,
            current_url=current_url,
            proxy=proxy,
            phone_pool=phone_pool,
            phone_probe_only=phone_probe_only,
        )
        totp_result.setdefault("protocol_stage", "totp")
        return totp_result
    elif stage == "add_phone":
        final = _finish_authorization(session, oauth, did, current_url, proxy=proxy, phone_pool=phone_pool, phone_probe_only=phone_probe_only)
        if final.get("ok"):
            final.setdefault("protocol_stage", "add_phone")
            return final
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": (final.get("phone_attempt") or {}).get("error") or "codex_oauth_add_phone_required",
            "last_url": final.get("last_url") or _safe_url(current_url),
            "phone_attempt": final.get("phone_attempt"),
            "protocol_stage": "add_phone",
        }

    email_otp_result = {
        "ok": False,
        "error": f"codex_oauth_protocol_stage_not_ready:{stage}",
        "last_url": _safe_url(current_url),
        "protocol_stage": stage,
    }

    final = _finish_authorization(session, oauth, did, current_url, proxy=proxy, phone_pool=phone_pool, phone_probe_only=phone_probe_only)
    if final.get("ok"):
        final.setdefault("protocol_stage", stage)
        return final

    return {
        "ok": False,
        "mode": "codex_oauth_pkce",
        "error": email_otp_result.get("error") or "oauth_callback_code_not_reached",
        "last_url": final.get("last_url") or _safe_url(current_url),
        "email_otp_attempt": email_otp_result,
        "phone_attempt": final.get("phone_attempt"),
        "protocol_stage": stage,
    }


def _passwordless_login_and_exchange(
    session,
    oauth,
    data,
    did,
    current_url,
    proxy=None,
    timeout=180,
    reason="",
    phone_pool=None,
    phone_probe_only=False,
    initial_otp_requested=False,
    otp_issued_after_unix=None,
):
    mailbox = _mailbox_from_data(data)
    if mailbox is None:
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": "passwordless_missing_mailbox",
            "fallback_from": reason,
            "last_url": _safe_url(current_url),
        }

    prime_result = _prime_email_verification_page(session, did, current_url)
    if not prime_result.get("ok"):
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": "email_verification_page_failed",
            "fallback_from": reason,
            "last_url": _safe_url(prime_result.get("url") or current_url),
            "body": prime_result.get("error") or "",
            "needs_session_restart": True,
        }

    issued_after = int(otp_issued_after_unix or time.time())
    pending_send = False
    authorize_continue_started_otp = bool(
        initial_otp_requested and _needs_email_otp(current_url)
    )
    if authorize_continue_started_otp:
        delivery_attempts = [{"operation": "authorize_continue", "status_code": 200}]
    else:
        # authorize/continue only starts OTP delivery when it lands on the
        # verification page. A password-page takeover needs an explicit send.
        send_result = _send_passwordless_otp(session, did, current_url)
        delivery_attempts = [
            {"operation": "send", "status_code": int(send_result.get("status_code") or 0)}
        ]
        pending_send = int(send_result.get("status_code") or 0) == 409
        if send_result.get("needs_session_restart"):
            return {
                "ok": False,
                "mode": "codex_oauth_pkce",
                "error": send_result.get("error") or "passwordless_send_invalid_state",
                "fallback_from": reason,
                "last_url": _safe_url(current_url),
                "body": send_result.get("body") or "",
                "needs_session_restart": True,
            }
        if send_result.get("hard_error"):
            return {
                "ok": False,
                "mode": "codex_oauth_pkce",
                "error": send_result.get("error", "passwordless_send_failed"),
                "fallback_from": reason,
                "last_url": _safe_url(current_url),
            }
    email_cfg = CFG.get("email_registration") or {}
    attempts = max(1, min(int(email_cfg.get("max_otp_retries") or 3), 5))
    requested_poll_timeout = max(int(timeout or 180), 1)
    provider = str(getattr(mailbox, "provider", "") or "").strip().lower()
    if provider == "url_html":
        try:
            url_mailbox_timeout = max(
                1,
                int(email_cfg.get("url_html_otp_timeout_seconds") or 300),
            )
        except (TypeError, ValueError):
            url_mailbox_timeout = 300
        total_poll_timeout = min(
            max(requested_poll_timeout, url_mailbox_timeout),
            1800,
        )
        if total_poll_timeout > requested_poll_timeout:
            print(
                "[*] URL mailbox OTP wait extended: "
                f"{requested_poll_timeout}s -> {total_poll_timeout}s"
            )
    else:
        total_poll_timeout = min(requested_poll_timeout, 300)
    attempt_poll_timeout = max(1, total_poll_timeout // attempts)
    last_error = ""
    last_validate_body = ""
    for attempt in range(attempts):
        if attempt > 0:
            resend_result = _resend_email_otp(session, did, current_url)
            delivery_attempts.append(
                {"operation": "resend", "status_code": int(resend_result.get("status_code") or 0)}
            )
            if int(resend_result.get("status_code") or 0) == 409:
                pending_send = True
                print("[*] Email OTP resend already pending; keeping previous OTP search window")
        try:
            code = _poll_email_otp(
                mailbox,
                subject_keyword=LOGIN_EMAIL_OTP_SUBJECT_KEYWORD,
                timeout=attempt_poll_timeout,
                issued_after_unix=issued_after,
                proxy=proxy,
            )
        except MailboxTokenExpiredError:
            return {
                "ok": False,
                "mode": "codex_oauth_pkce",
                "error": "mailbox_token_expired",
                "terminal": True,
                "fallback_from": reason,
                "last_url": _safe_url(current_url),
                "message": f"Mailbox refresh token expired for {getattr(mailbox, 'email', '')}. Re-authenticate the mailbox.",
            }
        if not code:
            last_error = "passwordless_email_otp_poll_timeout"
            continue
        validate = session.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            headers=with_sentinel(
                _oai_headers(did, {"Referer": "https://auth.openai.com/email-verification", "content-type": "application/json"}),
                load_cached_sentinel(),
            ),
            json={"code": code},
            timeout=30,
            impersonate=auth_impersonate(),
        )
        if validate.status_code != 200:
            last_error = f"email_otp_validate_failed:{validate.status_code}"
            last_validate_body = validate.text[:300]
            print(f"[*] Email OTP validate failed: {validate.status_code} {last_validate_body}")
            if _is_account_deactivated_response(validate.status_code, validate.text):
                return {
                    "ok": False,
                    "mode": "codex_oauth_pkce",
                    "error": "account_deactivated",
                    "terminal": True,
                    "fallback_from": reason,
                    "last_url": _safe_url(current_url),
                    "body": last_validate_body,
                }
            if "invalid_state" in last_validate_body:
                return {
                    "ok": False,
                    "mode": "codex_oauth_pkce",
                    "error": "email_otp_invalid_state",
                    "fallback_from": reason,
                    "last_url": _safe_url(current_url),
                    "body": last_validate_body,
                    "needs_session_restart": True,
                }
            continue
        next_url = _next_url(validate)
        _, current_url = _follow_redirects(session, next_url, proxy=proxy)
        final = _finish_authorization(session, oauth, did, current_url, proxy=proxy, phone_pool=phone_pool, phone_probe_only=phone_probe_only)
        if final.get("ok"):
            final["login_method"] = "passwordless_email_otp"
            final["fallback_from"] = reason
            return final
        if final.get("phone_attempt"):
            phone_error = (final.get("phone_attempt") or {}).get("error", "phone_verification_failed")
            return {
                "ok": False,
                "mode": "codex_oauth_pkce",
                "error": phone_error,
                "fallback_from": reason,
                "last_url": final.get("last_url") or _safe_url(current_url),
                "phone_attempt": final.get("phone_attempt"),
            }
        if current_url.endswith("/about-you"):
            return {
                "ok": False,
                "mode": "codex_oauth_pkce",
                "error": "passwordless_about_you_required",
                "fallback_from": reason,
                "last_url": _safe_url(current_url),
            }
    delivery_accepted = any(item["status_code"] == 200 for item in delivery_attempts)
    delivery_error = last_error or "passwordless_email_otp_failed"
    delivery_message = ""
    if last_error == "passwordless_email_otp_poll_timeout" and delivery_accepted:
        delivery_error = "passwordless_email_otp_not_delivered"
        delivery_message = (
            "OpenAI accepted the email OTP request, but the mailbox did not receive "
            "a new verification message within the configured timeout."
        )
        print("[*] Email OTP request accepted, but no new mailbox message was delivered")
    return {
        "ok": False,
        "mode": "codex_oauth_pkce",
        "error": delivery_error,
        "fallback_from": reason,
        "last_url": _safe_url(current_url),
        "body": last_validate_body,
        "message": delivery_message,
        "otp_delivery": {"attempts": delivery_attempts},
        "needs_session_restart": bool(pending_send and last_error == "passwordless_email_otp_poll_timeout"),
    }


def _password_login_and_exchange(session, oauth, data, did, current_url, proxy=None, timeout=180, phone_pool=None, phone_probe_only=False):
    password = _account_password_from_data(data)
    if not password:
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": "password_login_required",
            "last_url": _safe_url(current_url),
            "message": "OpenAI routed this account to password login, but the local session data has no account password.",
        }

    response = session.post(
        "https://auth.openai.com/api/accounts/password/verify",
        headers=with_sentinel(
            _oai_headers(did, {"Referer": current_url, "content-type": "application/json"}),
            load_cached_sentinel(),
        ),
        json={"password": password},
        timeout=30,
        impersonate=auth_impersonate(),
    )
    if response.status_code != 200:
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": f"password_verify_failed:{response.status_code}",
            "last_url": _safe_url(current_url),
            "body": response.text[:300],
        }

    _, current_url = _follow_redirects(session, _next_url(response), proxy=proxy)
    if _has_callback_code(current_url):
        return {
            "ok": True,
            "tokens": _exchange_callback(current_url, oauth, proxy=proxy),
            "login_method": "password",
        }

    if _needs_totp(current_url) or _response_requires_totp(response):
        totp_result = _complete_totp(session, data, did, current_url)
        if not totp_result.get("ok"):
            return totp_result
        _, current_url = _follow_redirects(session, totp_result.get("next_url", current_url), proxy=proxy)

    if _needs_email_otp(current_url):
        email_otp_result = _complete_email_otp(session, data, did, current_url, proxy=proxy, timeout=timeout)
        if not email_otp_result.get("ok"):
            return email_otp_result
        _, current_url = _follow_redirects(session, email_otp_result.get("next_url", ""), proxy=proxy)

    final = _finish_authorization(session, oauth, did, current_url, proxy=proxy, phone_pool=phone_pool, phone_probe_only=phone_probe_only)
    if final.get("ok"):
        final["login_method"] = "password"
        return final
    phone_attempt = final.get("phone_attempt") if isinstance(final.get("phone_attempt"), dict) else {}
    if phone_attempt.get("error"):
        final["error"] = phone_attempt.get("error")
    final.setdefault("mode", "codex_oauth_pkce")
    final.setdefault("error", "password_login_oauth_callback_not_reached")
    return final


def _account_mfa_data(data):
    mailbox = data.get("mailbox") if isinstance(data, dict) and isinstance(data.get("mailbox"), dict) else {}
    if str(mailbox.get("provider") or "").strip().lower() == "account_mfa":
        return mailbox
    return data if isinstance(data, dict) else {}


def _account_password_from_data(data):
    account_data = _account_mfa_data(data)
    return str(account_data.get("password") or "").strip()


def _totp_secret_from_data(data):
    account_data = _account_mfa_data(data)
    return str(account_data.get("totp_secret") or "").strip()


def _response_requires_totp(response):
    try:
        body = response.json()
    except Exception:
        return False
    if not isinstance(body, dict):
        return False
    for key in ("mfa_required", "requires_mfa", "mfaRequired", "requiresMfa", "mfa_pending"):
        if body.get(key) is True:
            return True
    nested = body.get("error") if isinstance(body.get("error"), dict) else {}
    return any(nested.get(key) is True for key in ("mfa_required", "requires_mfa", "mfaRequired", "requiresMfa"))


def _mfa_challenge_id(url):
    """Extract the challenge identifier embedded in an MFA challenge URL."""
    try:
        path = urllib.parse.urlparse(str(url or "")).path
    except Exception:
        return ""
    parts = [urllib.parse.unquote(part).strip() for part in path.split("/") if part.strip()]
    for index, part in enumerate(parts[:-1]):
        if part.lower() == "mfa-challenge":
            return parts[index + 1]
    return ""


def _complete_totp(session, data, did, current_url, timeout=30):
    secret = _totp_secret_from_data(data)
    if not secret:
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": "totp_required_missing_secret",
            "last_url": _safe_url(current_url),
            "message": "OpenAI requested authenticator MFA, but no local TOTP secret is configured.",
        }
    challenge_id = _mfa_challenge_id(current_url)
    if not challenge_id:
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": "totp_challenge_missing_id",
            "last_url": _safe_url(current_url),
            "message": "OpenAI requested authenticator MFA, but the challenge URL did not contain an id.",
        }
    try:
        code = generate_totp(secret)
    except ValueError:
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": "totp_secret_invalid",
            "last_url": _safe_url(current_url),
            "message": "The local TOTP secret is not a valid Base32 value.",
        }

    headers = with_sentinel(
        _oai_headers(
            did,
            {
                "Referer": current_url,
                "Origin": "https://auth.openai.com",
                "content-type": "application/json",
            },
        ),
        load_cached_sentinel(),
    )
    try:
        challenge = session.post(
            "https://auth.openai.com/api/accounts/mfa/issue_challenge",
            headers=headers,
            json={"type": "totp", "id": challenge_id},
            timeout=timeout,
            impersonate=auth_impersonate(),
        )
        if int(challenge.status_code or 0) != 200:
            return {
                "ok": False,
                "mode": "codex_oauth_pkce",
                "error": f"totp_challenge_failed:{challenge.status_code}",
                "last_url": _safe_url(current_url),
                "body": challenge.text[:300],
            }
        verify = session.post(
            "https://auth.openai.com/api/accounts/mfa/verify",
            headers=headers,
            json={"code": code, "type": "totp", "id": challenge_id},
            timeout=timeout,
            impersonate=auth_impersonate(),
        )
    except Exception as exc:
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": f"totp_verify_error:{exc}",
            "last_url": _safe_url(current_url),
        }
    if int(verify.status_code or 0) != 200:
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": f"totp_verify_failed:{verify.status_code}",
            "last_url": _safe_url(current_url),
            "body": verify.text[:300],
        }
    print("[*] ChatGPT authenticator MFA verified")
    return {"ok": True, "next_url": _next_url(verify) or current_url}


def _complete_totp_and_finish(session, oauth, data, did, current_url, proxy=None, phone_pool=None, phone_probe_only=False):
    totp_result = _complete_totp(session, data, did, current_url)
    if not totp_result.get("ok"):
        return totp_result
    _, next_url = _follow_redirects(session, totp_result.get("next_url") or current_url, proxy=proxy)
    final = _finish_authorization(session, oauth, did, next_url, proxy=proxy, phone_pool=phone_pool, phone_probe_only=phone_probe_only)
    if final.get("ok"):
        final["login_method"] = "password_totp"
        return final
    final.setdefault("mode", "codex_oauth_pkce")
    final.setdefault("error", "totp_oauth_callback_not_reached")
    return final


def _prime_email_verification_page(session, did, current_url):
    """Load the verification page before issuing the first OTP request."""
    if not _needs_email_otp(current_url):
        return {"ok": True, "url": current_url, "skipped": True}
    url = _absolute_url("https://auth.openai.com", current_url)
    try:
        response = request_with_retry(
            session,
            "get",
            url,
            label="Email verification page",
            headers=with_sentinel(
                _oai_headers(
                    did,
                    {
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Referer": url,
                    },
                ),
                load_cached_sentinel(),
            ),
            allow_redirects=False,
            impersonate=auth_impersonate(),
        )
        status = int(getattr(response, "status_code", 0) or 0)
        print(f"[*] Email verification page: {status}")
        location = ""
        try:
            location = urllib.parse.urljoin(url, str((response.headers or {}).get("Location") or ""))
        except Exception:
            location = ""
        if status in (200, 204, 304) or _needs_email_otp(location):
            return {"ok": True, "status_code": status, "url": url}
        return {"ok": False, "status_code": status, "url": location or url}
    except Exception as exc:
        print(f"[WARN] Email verification page failed: {exc}")
        return {"ok": False, "url": url, "error": str(exc)}


def _email_otp_request_headers(did, referer, sentinel):
    return with_sentinel(
        _oai_headers(
            did,
            {
                "Accept": "*/*",
                "Origin": "https://auth.openai.com",
                "Referer": referer or "https://auth.openai.com/email-verification",
                "content-type": "application/json",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
        ),
        sentinel,
    )


def _send_passwordless_otp(session, did, current_url):
    sentinel = load_cached_sentinel()
    pending_result = None
    for endpoint in (
        "https://auth.openai.com/api/accounts/email-otp/send",
        "https://auth.openai.com/api/accounts/passwordless/send-otp",
    ):
        try:
            response = session.post(
                endpoint,
                headers=_email_otp_request_headers(did, current_url, sentinel),
                json={},
                timeout=30,
                impersonate=auth_impersonate(),
            )
            if response.status_code == 200:
                detail = _otp_response_detail(response)
                print(f"[*] Passwordless OTP send accepted: {endpoint.rsplit('/', 1)[-1]}{detail}")
                return {
                    "ok": True,
                    "endpoint": endpoint,
                    "status_code": response.status_code,
                    "body": response.text[:300],
                }
            print(f"[*] Passwordless OTP send skipped: {endpoint.rsplit('/', 1)[-1]} {response.status_code}")
            if _is_invalid_auth_state_response(response.status_code, response.text):
                print("[*] Passwordless OTP auth session is invalid; restarting OAuth flow")
                return {
                    "ok": False,
                    "endpoint": endpoint,
                    "status_code": response.status_code,
                    "error": "passwordless_send_invalid_state",
                    "body": response.text[:300],
                    "needs_session_restart": True,
                }
            if response.status_code == 409:
                pending_result = {"ok": True, "endpoint": endpoint, "status_code": response.status_code}
                print("[*] Passwordless OTP send already pending; trying compatible send endpoint")
                continue
            if response.status_code not in (400, 404, 405):
                return {
                    "ok": False,
                    "hard_error": True,
                    "error": f"passwordless_send_failed:{response.status_code}",
                    "body": response.text[:300],
                }
        except Exception as exc:
            return {"ok": False, "hard_error": True, "error": f"passwordless_send_error:{exc}"}
    if pending_result is not None:
        print("[*] Passwordless OTP send remains pending; continuing to mailbox polling")
        return pending_result
    return {"ok": False, "error": "passwordless_send_unavailable"}


def _resend_email_otp(session, did, current_url):
    sentinel = load_cached_sentinel()
    try:
        response = session.post(
            "https://auth.openai.com/api/accounts/email-otp/resend",
            headers=_email_otp_request_headers(did, "https://auth.openai.com/email-verification", sentinel),
            json={},
            timeout=20,
            impersonate=auth_impersonate(),
        )
        detail = _otp_response_detail(response)
        print(f"[*] Email OTP resend: {response.status_code}{detail}")
        return {
            "ok": response.status_code in (200, 409),
            "status_code": response.status_code,
            "body": response.text[:300],
        }
    except Exception:
        return {"ok": False, "status_code": 0}


def _finish_authorization(session, oauth, did, current_url, proxy=None, phone_pool=None, phone_probe_only=False):
    if _has_callback_code(current_url):
        return {"ok": True, "tokens": _exchange_callback(current_url, oauth, proxy=proxy)}

    workspace_result = _select_workspace_if_needed(session, did, current_url, proxy=proxy)
    if workspace_result.get("ok"):
        current_url = workspace_result.get("url", current_url)
        if _has_callback_code(current_url):
            return {"ok": True, "tokens": _exchange_callback(current_url, oauth, proxy=proxy)}

    if _needs_phone_verification(current_url):
        if phone_probe_only:
            return {
                "ok": False,
                "error": "add_phone_required",
                "phone_verification_required": True,
                "last_url": _safe_url(current_url),
                "phone_attempt": {
                    "ok": False,
                    "error": "add_phone_required",
                    "message": "OAuth scan detected phone verification; SMS sending is disabled in probe mode.",
                },
            }
        if phone_pool:
            print("[*] Waiting for single-phone OAuth lane...")
            with phone_pool.lock:
                return _finish_phone_authorization_locked(session, oauth, did, current_url, proxy=proxy, phone_pool=phone_pool)
        return _finish_phone_authorization_locked(session, oauth, did, current_url, proxy=proxy, phone_pool=phone_pool)

    return {"ok": False, "last_url": _safe_url(current_url)}


def _finish_phone_authorization_locked(session, oauth, did, current_url, proxy=None, phone_pool=None):
    phone_result = complete_phone_verification(
        session,
        did,
        current_url,
        proxy=proxy,
        enabled=bool(phone_pool) or _auto_phone_verification(),
        phone_pool=phone_pool,
    )
    if phone_result.get("ok"):
        current_url = phone_result.get("url") or phone_result.get("next_url") or current_url
        try:
            _, current_url = _follow_redirects(session, current_url, proxy=proxy)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"phone_verified_oauth_redirect_failed:{exc}",
                "last_url": _safe_url(current_url),
                "phone_attempt": phone_result,
            }
        if _has_callback_code(current_url):
            try:
                tokens = _exchange_callback(current_url, oauth, proxy=proxy)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"phone_verified_oauth_token_exchange_failed:{exc}",
                    "last_url": _safe_url(current_url),
                    "phone_attempt": phone_result,
                }
            return {"ok": True, "tokens": tokens, "phone_attempt": phone_result}
        workspace_result = _select_workspace_if_needed(session, did, current_url, proxy=proxy)
        if workspace_result.get("ok"):
            current_url = workspace_result.get("url", current_url)
            if _has_callback_code(current_url):
                try:
                    tokens = _exchange_callback(current_url, oauth, proxy=proxy)
                except Exception as exc:
                    return {
                        "ok": False,
                        "error": f"phone_verified_oauth_token_exchange_failed:{exc}",
                        "last_url": _safe_url(current_url),
                        "phone_attempt": phone_result,
                    }
                return {"ok": True, "tokens": tokens, "phone_attempt": phone_result}
    return {"ok": False, "last_url": _safe_url(current_url), "phone_attempt": phone_result}


def _complete_email_otp(session, data, did, current_url, proxy=None, timeout=180):
    mailbox = _mailbox_from_data(data)
    if mailbox is None:
        return {"ok": False, "mode": "codex_oauth_pkce", "error": "email_otp_required_missing_mailbox"}
    issued_after = int(time.time())
    try:
        session.post(
            "https://auth.openai.com/api/accounts/email-otp/send",
            headers=_oai_headers(did, {"Referer": current_url, "content-type": "application/json"}),
            json={},
            timeout=30,
            impersonate=auth_impersonate(),
        )
    except Exception:
        pass
    code = _poll_email_otp(
        mailbox,
        subject_keyword=LOGIN_EMAIL_OTP_SUBJECT_KEYWORD,
        timeout=min(max(int(timeout or 180), 30), 300),
        issued_after_unix=issued_after,
        proxy=proxy,
    )
    if not code:
        return {"ok": False, "mode": "codex_oauth_pkce", "error": "email_otp_poll_timeout"}
    validate = session.post(
        "https://auth.openai.com/api/accounts/email-otp/validate",
        headers=with_sentinel(
            _oai_headers(did, {"Referer": "https://auth.openai.com/email-verification", "content-type": "application/json"}),
            load_cached_sentinel(),
        ),
        json={"code": code},
        timeout=30,
        impersonate=auth_impersonate(),
    )
    if validate.status_code != 200:
        return {
            "ok": False,
            "mode": "codex_oauth_pkce",
            "error": f"email_otp_validate_failed:{validate.status_code}",
            "body": validate.text[:300],
        }
    return {"ok": True, "next_url": _next_url(validate)}


def _select_workspace_if_needed(session, did, current_url, proxy=None):
    if not current_url or not (current_url.endswith("/consent") or current_url.endswith("/workspace")):
        return {"ok": False}
    workspaces = _parse_workspace_from_auth_cookie(_cookie_value(session, "oai-client-auth-session"))
    if not workspaces:
        return {"ok": False}
    workspace_id = ""
    for item in workspaces:
        title = str(item.get("title") or item.get("name") or "")
        if item.get("is_personal") or "Personal" in title:
            workspace_id = str(item.get("id") or "")
            break
    workspace_id = workspace_id or str((workspaces[0] or {}).get("id") or "")
    if not workspace_id:
        return {"ok": False}
    selected = _select_workspace(session, did, current_url, workspace_id, proxy=proxy)
    if not selected.get("ok"):
        return selected
    _, final_url = _follow_redirects(session, selected.get("next_url") or current_url, proxy=proxy)
    return {"ok": True, "url": final_url, "workspace_id": workspace_id}


def _select_workspace(session, did, referer, workspace_id, proxy=None, timeout=30, extra_headers=None):
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        return {"ok": False, "error": "missing_workspace_id"}
    headers = with_sentinel(
        _oai_headers(did, {"Referer": referer or "https://auth.openai.com/workspace", "content-type": "application/json"}),
        load_cached_sentinel(),
    )
    if isinstance(extra_headers, dict):
        headers.update(extra_headers)
    response = session.post(
        "https://auth.openai.com/api/accounts/workspace/select",
        headers=headers,
        json={"workspace_id": workspace_id},
        timeout=timeout,
        impersonate=auth_impersonate(),
    )
    if response.status_code != 200:
        return {
            "ok": False,
            "workspace_id": workspace_id,
            "status": response.status_code,
            "body": (response.text or "")[:300],
            "error": f"workspace_select_failed:{response.status_code}",
        }
    return {"ok": True, "workspace_id": workspace_id, "status": response.status_code, "next_url": _next_url(response)}


def select_workspace_by_cookie(cookie_header, workspace_id, device_id="", proxy=None, timeout=30, referer="https://auth.openai.com/workspace"):
    """Select an auth workspace using saved cookies.

    This is the protocol adapter reused by one-click scan when it needs to move
    a live browser/session cookie to a fallback workspace before refreshing
    /api/auth/session.
    """
    cookie_header = str(cookie_header or "").strip()
    if not cookie_header:
        return {"ok": False, "workspace_id": str(workspace_id or "").strip(), "error": "missing_cookie_header"}
    session = curl_requests.Session()
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    # curl_cffi accepts explicit Cookie headers more reliably than importing a
    # mixed-domain cookie jar from a serialized browser cookie string.
    return _select_workspace(
        session,
        device_id or "scan",
        referer,
        workspace_id,
        proxy=proxy,
        timeout=timeout,
        extra_headers={"cookie": cookie_header},
    )


def _new_oauth_request():
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": ORIGINATOR,
    }
    return {
        "state": state,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
        "auth_url": f"{AUTH_URL}?{urllib.parse.urlencode(params)}",
    }


def _exchange_callback(callback_url, oauth, proxy=None):
    parsed = urllib.parse.urlparse(callback_url)
    query = urllib.parse.parse_qs(parsed.query)
    code = (query.get("code") or [""])[0]
    state = (query.get("state") or [""])[0]
    if not code:
        raise RuntimeError("oauth_callback_missing_code")
    if state != oauth["state"]:
        raise RuntimeError("oauth_state_mismatch")
    response = request_with_retry(
        curl_requests,
        "post",
        TOKEN_URL,
        label="OAuth token exchange",
        attempts=5,
        retry_delay=5,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": oauth["redirect_uri"],
            "code_verifier": oauth["code_verifier"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        proxies={"http": proxy, "https": proxy} if proxy else None,
        impersonate=auth_impersonate(),
    )
    if response.status_code != 200:
        raise RuntimeError(f"oauth_token_exchange_failed:{response.status_code}:{response.text[:300]}")
    body = response.json()
    if not body.get("access_token") or not body.get("refresh_token"):
        raise RuntimeError("oauth_token_response_missing_access_or_refresh_token")
    return body


def _save_oauth_tokens(data, json_path, tokens, email, mode, result=None):
    now = int(time.time())
    expires_in = _as_int(tokens.get("expires_in")) or 0
    refreshed = dict(data)
    refreshed["email"] = email
    refreshed["success"] = True
    refreshed["access_token"] = tokens.get("access_token", "")
    refreshed["id_token"] = tokens.get("id_token", "")
    refreshed["oauth_refresh_token"] = tokens.get("refresh_token", "")
    refreshed["refresh_token_status"] = "oauth_present"
    refreshed["refresh_token_updated_at"] = now
    refreshed["refreshed_at"] = now
    refreshed.pop("error", None)
    if str(refreshed.get("status") or "").lower() == "failed":
        refreshed.pop("status", None)
    refreshed["codex_oauth"] = {
        "client_id": CLIENT_ID,
        "scope": SCOPE,
        "mode": mode,
        "updated_at": now,
    }
    result = result if isinstance(result, dict) else {}
    phone_attempt = result.get("phone_attempt") if isinstance(result.get("phone_attempt"), dict) else {}
    if phone_attempt:
        refreshed["phone"] = phone_attempt.get("phone", refreshed.get("phone", ""))
        response = refreshed.get("response") if isinstance(refreshed.get("response"), dict) else {}
        response["phone_verification"] = phone_attempt
        response["codex_oauth"] = {
            "ok": True,
            "mode": mode,
            "has_access_token": bool(tokens.get("access_token")),
            "has_refresh_token": bool(tokens.get("refresh_token")),
            "phone_verified": bool(phone_attempt.get("ok")),
        }
        refreshed["response"] = response
    if expires_in:
        refreshed["oauth_expires_at"] = _iso_utc(now + expires_in)
    if json_path:
        from pathlib import Path
        Path(json_path).write_text(json.dumps(refreshed, ensure_ascii=False, indent=2), encoding="utf-8")
    upsert_account(refreshed, json_path=json_path)
    output = {
        "ok": True,
        "mode": mode,
        "email": email,
        "json_path": json_path,
        "refresh_token_status": "oauth_present",
    }
    if phone_attempt:
        output["phone"] = phone_attempt.get("phone", "")
        output["phone_attempt"] = phone_attempt
    return output


def _follow_redirects(session, start_url, proxy=None, max_redirects=18):
    current_url = _absolute_url("https://auth.openai.com", start_url)
    response = None
    for _ in range(max_redirects):
        if not current_url:
            return response, current_url
        response = request_with_retry(
            session,
            "get",
            current_url,
            label="OAuth redirect",
            attempts=5,
            retry_delay=5,
            allow_redirects=False,
            impersonate=auth_impersonate(),
        )
        if response.status_code not in (301, 302, 303, 307, 308):
            return response, current_url
        location = response.headers.get("Location", "")
        if not location:
            return response, current_url
        current_url = urllib.parse.urljoin(current_url, location)
        if _has_callback_code(current_url):
            return response, current_url
    return response, current_url


def _mailbox_from_data(data):
    mailbox = data.get("mailbox") if isinstance(data.get("mailbox"), dict) else {}
    email = str(mailbox.get("email") or data.get("email") or "").strip()
    refresh_token = str(mailbox.get("refresh_token") or "").strip()
    provider = str(mailbox.get("provider") or "").strip()
    if not email:
        return None
    mailbox_password = mailbox.get("password") if "password" in mailbox else data.get("password")
    result = MailboxAccount(
        email=email,
        password=str(mailbox_password or "").strip(),
        login_password=str(mailbox.get("login_password") or "").strip(),
        totp_secret=str(mailbox.get("totp_secret") or "").strip(),
        refresh_token=refresh_token,
        access_token=str(mailbox.get("access_token") or "").strip(),
        token=str(mailbox.get("token") or "").strip(),
        client_secret=str(mailbox.get("client_secret") or "").strip(),
        auth_mode=str(mailbox.get("auth_mode") or "").strip(),
        sender_name=str(mailbox.get("sender_name") or "").strip(),
        source=str(mailbox.get("source") or "").strip(),
        provider=provider,
        order_no=str(mailbox.get("order_no") or "").strip(),
        purchase_id=str(mailbox.get("purchase_id") or "").strip(),
        project_name=str(mailbox.get("project_name") or "").strip(),
        price=str(mailbox.get("price") or "").strip(),
        purchase_total_cost=str(mailbox.get("purchase_total_cost") or "").strip(),
        balance_after=str(mailbox.get("balance_after") or "").strip(),
        inbox_url=str(mailbox.get("inbox_url") or "").strip(),
    )
    if not mailbox_has_inbox_credentials(result):
        if mailbox_gmail.is_gmail_mailbox(result):
            from .mailbox import _mailbox_from_config

            fallback = _mailbox_from_config(argparse.Namespace(email=email))
            if fallback is not None and mailbox_has_inbox_credentials(fallback):
                return fallback
        return None
    return result


def _is_terminal_account_data(data):
    if not isinstance(data, dict):
        return False
    terminal = data.get("terminal_failure") if isinstance(data.get("terminal_failure"), dict) else {}
    text = " ".join(str(value or "") for value in (
        data.get("status"),
        data.get("error"),
        data.get("account_scan_status"),
        terminal.get("code"),
        terminal.get("reason"),
    )).lower()
    return any(marker in text for marker in (
        "account_deactivated",
        "account_deatived",
        "deleted or deactivated",
        "account has been deleted",
        "account has been deactivated",
    ))


def _oai_headers(did, extra=None):
    return openai_auth_headers_lower(did, extra=extra)


def _next_url(response):
    try:
        body = response.json()
    except Exception:
        body = {}
    return _absolute_url("https://auth.openai.com", body.get("continue_url") or response.headers.get("Location") or response.url)


def _needs_email_otp(url):
    value = str(url or "").lower()
    return "email-verification" in value or "email-otp" in value


def _needs_totp(url):
    value = str(url or "").lower()
    return any(marker in value for marker in ("/mfa", "multi-factor", "two-factor", "/2fa"))


def _needs_password(url):
    value = str(url or "").lower()
    return "/log-in/password" in value or "/login/password" in value or value.endswith("/password")


def _needs_phone_verification(url):
    value = str(url or "").lower()
    return "/add-phone" in value or "phone-verification" in value


def _detect_protocol_stage(url):
    value = str(url or "")
    lower = value.lower()
    if _has_callback_code(value):
        return "callback"
    if _needs_phone_verification(lower):
        return "add_phone"
    if _needs_email_otp(lower):
        return "email_otp"
    if _needs_totp(lower):
        return "totp"
    if _needs_password(lower):
        return "password"
    if lower.endswith("/consent") or lower.endswith("/workspace"):
        return "consent"
    if "/authorize" in lower or "/oauth/" in lower:
        return "email"
    return "unknown"


def _codex_oauth_protocol_ready_stage(stage):
    return stage in {"callback", "consent"}


def _codex_oauth_cfg():
    return CFG.get("codex_oauth") if isinstance(CFG.get("codex_oauth"), dict) else {}


def _allow_passwordless_takeover():
    return bool(_codex_oauth_cfg().get("allow_passwordless_takeover", False))


def _auto_phone_verification():
    return bool(_codex_oauth_cfg().get("auto_phone_verification", False))


def _is_account_deactivated_response(status_code, text):
    body = str(text or "").lower()
    return int(status_code or 0) in (403, 404) and (
        "account_deactivated" in body
        or "deleted or deactivated" in body
        or "account has been deleted" in body
        or "account has been deactivated" in body
    )


def _is_invalid_auth_state_response(status_code, text):
    if int(status_code or 0) not in (400, 409):
        return False
    body = str(text or "").lower()
    return "invalid_state" in body or "sign-in session is no longer valid" in body


def _otp_response_detail(response):
    parts = []
    try:
        body = response.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        if "success" in body:
            parts.append(f"success={str(bool(body.get('success'))).lower()}")
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = str(error.get("code") or "").strip()
        if code:
            parts.append(f"code={code}")
    try:
        raw_retry_after = (response.headers or {}).get("Retry-After")
        retry_after = str(raw_retry_after).strip() if isinstance(raw_retry_after, (str, int, float)) else ""
    except Exception:
        retry_after = ""
    if retry_after:
        parts.append(f"retry_after={retry_after}")
    return f" ({', '.join(parts)})" if parts else ""


def _has_callback_code(url):
    text = str(url or "")
    return "code=" in text and "state=" in text


def _cookie_value(session, name):
    try:
        return session.cookies.get(name) or ""
    except Exception:
        return ""


def _clear_auth_session_cookies(session):
    for domain in ("auth.openai.com", ".auth.openai.com", ".openai.com", "openai.com"):
        try:
            session.cookies.clear(domain=domain)
        except Exception:
            pass


def _parse_workspace_from_auth_cookie(auth_cookie):
    if not auth_cookie or "." not in auth_cookie:
        return []
    parts = auth_cookie.split(".")
    for segment in parts[1:2] + parts[:1]:
        claims = _jwt_segment(segment)
        workspaces = claims.get("workspaces") or []
        if isinstance(workspaces, list) and workspaces:
            return workspaces
    return []


def _jwt_segment(segment):
    try:
        padded = segment + "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def _absolute_url(base_url, url):
    if not url:
        return ""
    if str(url).startswith(("http://", "https://")):
        return str(url)
    return base_url.rstrip("/") + "/" + str(url).lstrip("/")


def _safe_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    except Exception:
        return str(url or "")[:200]


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _as_int(value):
    try:
        return int(value)
    except Exception:
        return 0


def _iso_utc(epoch_seconds):
    return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
