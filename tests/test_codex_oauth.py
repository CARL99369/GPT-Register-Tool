import unittest
import urllib.parse
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from sms_tool import codex_oauth


class CodexOauthTests(unittest.TestCase):
    def test_oauth_authorize_url_matches_current_codex_cli_contract(self):
        oauth = codex_oauth._new_oauth_request()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(oauth["auth_url"]).query)

        self.assertEqual(query["client_id"], [codex_oauth.CLIENT_ID])
        self.assertEqual(query["originator"], ["codex_cli_rs"])
        self.assertEqual(
            query["scope"],
            ["openid profile email offline_access api.connectors.read api.connectors.invoke"],
        )
        self.assertEqual(query["codex_cli_simplified_flow"], ["true"])

    def test_authorize_continue_declares_login_or_signup_screen_hint(self):
        session = Mock()
        session.cookies.set = Mock()
        session.post.return_value = Mock(status_code=200, text="{}")

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}), \
             patch("sms_tool.codex_oauth.attach_sentinel"), \
             patch("sms_tool.codex_oauth._next_url", return_value="https://auth.openai.com/email-verification"), \
             patch("sms_tool.codex_oauth._follow_redirects", return_value=(None, "https://auth.openai.com/email-verification")), \
             patch("sms_tool.codex_oauth.time.time", return_value=1234), \
             patch("sms_tool.codex_oauth._run_protocol_login_stages", return_value={"ok": False, "error": "stop"}) as stages:
            codex_oauth._login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                email="user@example.com",
                data={"device_id": "did"},
                current_url="https://auth.openai.com/log-in-or-create-account",
                force_email_otp_login=True,
            )

        self.assertEqual(
            session.post.call_args.kwargs["json"],
            {
                "username": {"value": "user@example.com", "kind": "email"},
                "screen_hint": "login_or_signup",
            },
        )
        self.assertTrue(stages.call_args.kwargs["initial_otp_requested"])
        self.assertEqual(stages.call_args.kwargs["otp_issued_after_unix"], 1234)

    def test_collect_prepares_device_bound_sentinel_before_protocol_login(self):
        session = Mock()
        data = {"email": "user@example.com", "device_id": "device-123"}

        with patch("sms_tool.codex_oauth.select_auth_fingerprint"), \
             patch("sms_tool.codex_oauth._ensure_oauth_sentinel") as ensure, \
             patch("sms_tool.codex_oauth.curl_requests.Session", return_value=session), \
             patch("sms_tool.codex_oauth.import_cached_auth_cookies"), \
             patch("sms_tool.codex_oauth._follow_redirects", return_value=(None, "https://auth.openai.com/log-in")), \
             patch("sms_tool.codex_oauth._login_and_exchange", return_value={"ok": False, "error": "stop"}):
            codex_oauth.collect_codex_oauth_tokens(data, proxy="http://proxy.example:8080")

        ensure.assert_called_once_with(
            device_id="device-123",
            proxy="http://proxy.example:8080",
        )

    def test_oauth_sentinel_falls_back_to_http_for_same_device(self):
        sentinel = {"sentinel_token": "token", "oai_did": "device-123"}

        with patch("sms_tool.sentinel_tokens._get_cached_sentinel", return_value=None), \
             patch("sms_tool.sentinel_tokens._extract_sentinel_quickjs", return_value=None) as quickjs, \
             patch("sms_tool.sentinel_tokens._extract_sentinel_http", return_value=sentinel) as http:
            result = codex_oauth._ensure_oauth_sentinel(
                device_id="device-123",
                proxy="http://proxy.example:8080",
            )

        self.assertEqual(result, sentinel)
        quickjs.assert_called_once_with(
            proxy="http://proxy.example:8080",
            persist=True,
            device_id="device-123",
        )
        http.assert_called_once_with(
            proxy="http://proxy.example:8080",
            persist=True,
            device_id="device-123",
        )

    def test_refresh_skips_terminal_account_without_network(self):
        with patch("sms_tool.codex_oauth.collect_codex_oauth_tokens") as collect:
            result = codex_oauth.refresh_codex_oauth_session({
                "email": "user@example.com",
                "status": "account_deactivated",
            })

        self.assertFalse(result["ok"])
        self.assertTrue(result["terminal"])
        collect.assert_not_called()

    def test_refresh_can_collect_without_persisting(self):
        collected = {"ok": True, "tokens": {"access_token": "at_new", "refresh_token": "rt_new"}}
        with patch("sms_tool.codex_oauth.collect_codex_oauth_tokens", return_value=collected), \
             patch("sms_tool.codex_oauth._save_oauth_tokens") as save:
            result = codex_oauth.refresh_codex_oauth_session(
                {"email": "user@example.com"},
                persist=False,
                force_email_otp_login=True,
            )

        self.assertIs(result, collected)
        save.assert_not_called()

    def test_mailbox_from_data_falls_back_to_config_for_gmail(self):
        fallback = codex_oauth.MailboxAccount(
            email="secondary.user@gmail.com",
            provider="gmail",
            password="abcd efgh ijkl mnop",
            auth_mode="app_password",
            source="config",
        )
        with patch("sms_tool.codex_oauth.mailbox_has_inbox_credentials", side_effect=[False, True]), \
             patch("sms_tool.mailbox._mailbox_from_config", return_value=fallback) as from_config:
            result = codex_oauth._mailbox_from_data({"email": "secondary.user@gmail.com"})

        self.assertIs(result, fallback)
        from_config.assert_called_once()

    def test_mailbox_from_data_does_not_fallback_to_config_for_non_gmail(self):
        with patch("sms_tool.codex_oauth.mailbox_has_inbox_credentials", return_value=False), \
             patch("sms_tool.mailbox._mailbox_from_config") as from_config:
            result = codex_oauth._mailbox_from_data({"email": "user@example.com"})

        self.assertIsNone(result)
        from_config.assert_not_called()

    def test_cfworker_mailbox_does_not_inherit_chatgpt_account_password(self):
        data = {
            "email": "target@liziai.cloud",
            "password": "ChatGPTPassword!A1",
            "mailbox": {
                "email": "target@liziai.cloud",
                "provider": "cfworker",
                "password": "",
                "source": "https://worker.example",
            },
        }
        with patch("sms_tool.codex_oauth.mailbox_has_inbox_credentials", return_value=True):
            result = codex_oauth._mailbox_from_data(data)

        self.assertEqual(result.provider, "cfworker")
        self.assertEqual(result.password, "")

    def test_account_deactivated_response_is_terminal(self):
        body = '{"error":{"code":"account_deactivated","message":"You do not have an account because it has been deleted or deactivated."}}'

        self.assertTrue(codex_oauth._is_account_deactivated_response(403, body))
        self.assertFalse(codex_oauth._is_account_deactivated_response(401, body))
        self.assertFalse(codex_oauth._is_account_deactivated_response(403, '{"error":"wrong code"}'))

    def test_phone_verification_url_detection(self):
        self.assertTrue(codex_oauth._needs_phone_verification("https://auth.openai.com/add-phone"))
        self.assertTrue(codex_oauth._needs_phone_verification("https://auth.openai.com/phone-verification"))
        self.assertFalse(codex_oauth._needs_phone_verification("https://auth.openai.com/consent"))

    def test_phone_probe_only_does_not_complete_sms(self):
        with patch("sms_tool.codex_oauth.complete_phone_verification") as complete:
            result = codex_oauth._finish_authorization(
                Mock(),
                {"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                "did",
                "https://auth.openai.com/add-phone",
                phone_probe_only=True,
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["phone_verification_required"])
        self.assertEqual(result["error"], "add_phone_required")
        complete.assert_not_called()

    def test_protocol_stage_detection_matches_oauth_flow_urls(self):
        self.assertEqual(
            codex_oauth._detect_protocol_stage("http://localhost:1455/auth/callback?code=a&state=b"),
            "callback",
        )
        self.assertEqual(codex_oauth._detect_protocol_stage("https://auth.openai.com/consent"), "consent")
        self.assertEqual(codex_oauth._detect_protocol_stage("https://auth.openai.com/log-in/password"), "password")
        self.assertEqual(codex_oauth._detect_protocol_stage("https://auth.openai.com/email-verification"), "email_otp")
        self.assertEqual(codex_oauth._detect_protocol_stage("https://auth.openai.com/add-phone"), "add_phone")
        self.assertEqual(codex_oauth._detect_protocol_stage("https://auth.openai.com/mfa"), "totp")

    def test_password_login_reads_totp_secret_from_account_mfa_mailbox(self):
        session = Mock()
        response = Mock(status_code=200)
        session.post.return_value = response

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}), \
             patch("sms_tool.codex_oauth._next_url", return_value="https://auth.openai.com/mfa"), \
             patch("sms_tool.codex_oauth._follow_redirects", return_value=(None, "https://auth.openai.com/mfa")), \
             patch("sms_tool.codex_oauth._complete_totp", return_value={"ok": True, "next_url": "https://auth.openai.com/consent"}) as complete_totp, \
             patch("sms_tool.codex_oauth._finish_authorization", return_value={"ok": True, "tokens": {"access_token": "at", "refresh_token": "rt_1"}}):
            result = codex_oauth._password_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={
                    "email": "user@example.com",
                    "mailbox": {
                        "provider": "account_mfa",
                        "password": "Secret!A1",
                        "totp_secret": "JBSWY3DPEHPK3PXP",
                    },
                },
                did="did",
                current_url="https://auth.openai.com/log-in/password",
            )

        self.assertTrue(result["ok"])
        complete_totp.assert_called_once()

    def test_complete_totp_issues_challenge_and_verifies_local_code(self):
        session = Mock()
        issue = Mock(status_code=200, text='{}')
        verify = Mock(status_code=200, text='{}')
        session.post.side_effect = [issue, verify]

        with patch("sms_tool.codex_oauth.generate_totp", return_value="123456") as generate, \
             patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}), \
             patch("sms_tool.codex_oauth._next_url", return_value="https://auth.openai.com/consent"):
            result = codex_oauth._complete_totp(
                session,
                {"mailbox": {"provider": "account_mfa", "totp_secret": "JBSWY3DPEHPK3PXP"}},
                "did",
                "https://auth.openai.com/mfa-challenge/challenge-123",
            )

        self.assertTrue(result["ok"])
        generate.assert_called_once_with("JBSWY3DPEHPK3PXP")
        self.assertEqual(session.post.call_args_list[0].args[0], "https://auth.openai.com/api/accounts/mfa/issue_challenge")
        self.assertEqual(
            session.post.call_args_list[0].kwargs["json"],
            {"type": "totp", "id": "challenge-123"},
        )
        self.assertEqual(session.post.call_args_list[1].args[0], "https://auth.openai.com/api/accounts/mfa/verify")
        self.assertEqual(
            session.post.call_args_list[1].kwargs["json"],
            {"code": "123456", "type": "totp", "id": "challenge-123"},
        )

    def test_password_login_handles_mfa_required_response_before_redirect(self):
        session = Mock()
        password_response = Mock(status_code=200, text='{"mfa_required":true}')
        password_response.json.return_value = {"mfa_required": True}
        session.post.return_value = password_response

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}), \
             patch("sms_tool.codex_oauth._next_url", return_value="https://auth.openai.com/consent"), \
             patch("sms_tool.codex_oauth._follow_redirects", return_value=(None, "https://auth.openai.com/consent")), \
             patch("sms_tool.codex_oauth._complete_totp", return_value={"ok": True, "next_url": "https://auth.openai.com/consent"}) as complete_totp, \
             patch("sms_tool.codex_oauth._finish_authorization", return_value={"ok": True, "tokens": {"access_token": "at", "refresh_token": "rt_1"}}):
            result = codex_oauth._password_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"password": "Secret!A1", "totp_secret": "JBSWY3DPEHPK3PXP"},
                did="did",
                current_url="https://auth.openai.com/log-in/password",
            )

        self.assertTrue(result["ok"])
        complete_totp.assert_called_once()

    def test_logged_in_oauth_does_not_force_passwordless_otp(self):
        session = Mock()
        session.cookies.set = Mock()
        response = Mock(status_code=200)
        session.post.return_value = response

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}), \
             patch("sms_tool.codex_oauth.attach_sentinel"), \
             patch("sms_tool.codex_oauth._next_url", return_value="https://auth.openai.com/consent"), \
             patch("sms_tool.codex_oauth._follow_redirects", return_value=(None, "https://auth.openai.com/consent")), \
             patch("sms_tool.codex_oauth._passwordless_login_and_exchange") as passwordless, \
             patch("sms_tool.codex_oauth._finish_authorization", return_value={"ok": True, "tokens": {"access_token": "at", "refresh_token": "rt_1"}}) as finish:
            result = codex_oauth._login_and_exchange(
                session=session,
                oauth={"auth_url": "https://auth.openai.com/oauth/authorize", "state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                email="user@example.com",
                data={"device_id": "did"},
                current_url="https://auth.openai.com/authorize",
                force_email_otp_login=False,
            )

        self.assertTrue(result["ok"])
        finish.assert_called_once()
        passwordless.assert_not_called()

    def test_invalid_auth_state_reopens_oauth_authorize_before_retry(self):
        session = Mock()
        session.cookies.set = Mock()
        session.cookies.clear = Mock()
        session.post.return_value = Mock(status_code=200, text="{}")
        oauth = {
            "auth_url": "https://auth.openai.com/oauth/authorize?client_id=test",
            "state": "s",
            "code_verifier": "v",
            "redirect_uri": "http://localhost",
        }
        replacement_oauth = {
            "auth_url": "https://auth.openai.com/oauth/authorize?client_id=test&state=fresh",
            "state": "fresh",
            "code_verifier": "fresh-verifier",
            "redirect_uri": "http://localhost",
        }

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}), \
             patch("sms_tool.codex_oauth.attach_sentinel"), \
             patch("sms_tool.codex_oauth.secrets.token_hex", return_value="fresh-device-id"), \
             patch("sms_tool.codex_oauth._ensure_oauth_sentinel", return_value={
                 "sentinel_token": "fresh-sentinel",
                 "cookie_str": "",
             }), \
             patch("sms_tool.codex_oauth._new_oauth_request", return_value=replacement_oauth), \
             patch("sms_tool.codex_oauth._next_url", return_value="https://auth.openai.com/email-verification"), \
             patch("sms_tool.codex_oauth._follow_redirects", return_value=(None, "https://auth.openai.com/email-verification")) as follow, \
             patch("sms_tool.codex_oauth._run_protocol_login_stages", side_effect=[
                 {"ok": False, "error": "passwordless_send_invalid_state", "needs_session_restart": True},
                 {"ok": True, "tokens": {"access_token": "at", "refresh_token": "rt"}},
             ]):
            result = codex_oauth._login_and_exchange(
                session=session,
                oauth=oauth,
                email="user@example.com",
                data={"device_id": "did"},
                current_url="https://auth.openai.com/log-in",
                force_email_otp_login=True,
            )

        self.assertTrue(result["ok"])
        self.assertIn(
            replacement_oauth["auth_url"],
            [call.args[1] for call in follow.call_args_list],
        )
        cleared_domains = [call.kwargs.get("domain") for call in session.cookies.clear.call_args_list]
        self.assertIn("auth.openai.com", cleared_domains)
        self.assertIn(".auth.openai.com", cleared_domains)
        self.assertIn(".openai.com", cleared_domains)
        device_ids = [call.args[1] for call in session.cookies.set.call_args_list if call.args[0] == "oai-did"]
        self.assertEqual(device_ids, ["did", "fresh-device-id"])

    def test_authorize_continue_invalid_state_restarts_before_protocol_stages(self):
        session = Mock()
        session.cookies.set = Mock()
        session.cookies.clear = Mock()
        session.post.side_effect = [
            Mock(
                status_code=409,
                text='{"error":{"code":"invalid_state","message":"Your sign-in session is no longer valid."}}',
                headers={},
            ),
            Mock(status_code=200, text="{}", headers={}),
        ]

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}), \
             patch("sms_tool.codex_oauth.attach_sentinel"), \
             patch("sms_tool.codex_oauth._ensure_oauth_sentinel", return_value={
                 "sentinel_token": "fresh-sentinel",
                 "cookie_str": "",
             }) as ensure, \
             patch("sms_tool.codex_oauth._new_oauth_request", return_value={
                 "auth_url": "https://auth.openai.com/oauth/authorize?state=fresh",
                 "state": "fresh",
                 "code_verifier": "fresh-verifier",
                 "redirect_uri": "http://localhost",
             }), \
             patch("sms_tool.codex_oauth._next_url", return_value="https://auth.openai.com/email-verification"), \
             patch("sms_tool.codex_oauth._follow_redirects", return_value=(None, "https://auth.openai.com/email-verification")), \
             patch("sms_tool.codex_oauth._run_protocol_login_stages", return_value={
                 "ok": True,
                 "tokens": {"access_token": "at", "refresh_token": "rt"},
             }) as stages:
            result = codex_oauth._login_and_exchange(
                session=session,
                oauth={
                    "auth_url": "https://auth.openai.com/oauth/authorize?state=old",
                    "state": "old",
                    "code_verifier": "old-verifier",
                    "redirect_uri": "http://localhost",
                },
                email="user@example.com",
                data={"device_id": "did"},
                current_url="https://auth.openai.com/log-in",
                force_email_otp_login=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(stages.call_count, 1)
        self.assertGreaterEqual(ensure.call_count, 1)

    def test_authorize_continue_cloudflare_challenge_retries_with_fresh_sentinel(self):
        session = Mock()
        session.cookies.set = Mock()
        session.cookies.clear = Mock()
        session.post.side_effect = [
            Mock(
                status_code=403,
                text="<!doctype html><title>Just a moment...</title><div>cf-chl</div>",
                headers={},
            ),
            Mock(status_code=200, text="{}", headers={}),
        ]

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}), \
             patch("sms_tool.codex_oauth.attach_sentinel"), \
             patch("sms_tool.codex_oauth._ensure_oauth_sentinel", return_value={
                 "sentinel_token": "fresh-sentinel",
                 "cookie_str": "",
             }) as ensure, \
             patch("sms_tool.codex_oauth._new_oauth_request", return_value={
                 "auth_url": "https://auth.openai.com/oauth/authorize?state=fresh",
                 "state": "fresh",
                 "code_verifier": "fresh-verifier",
                 "redirect_uri": "http://localhost",
             }), \
             patch("sms_tool.codex_oauth._next_url", return_value="https://auth.openai.com/email-verification"), \
             patch("sms_tool.codex_oauth._follow_redirects", return_value=(None, "https://auth.openai.com/email-verification")), \
             patch("sms_tool.codex_oauth._run_protocol_login_stages", return_value={
                 "ok": True,
                 "tokens": {"access_token": "at", "refresh_token": "rt"},
             }), \
             patch("sms_tool.codex_oauth.time.sleep") as sleep:
            result = codex_oauth._login_and_exchange(
                session=session,
                oauth={
                    "auth_url": "https://auth.openai.com/oauth/authorize?state=old",
                    "state": "old",
                    "code_verifier": "old-verifier",
                    "redirect_uri": "http://localhost",
                },
                email="user@example.com",
                data={"device_id": "did"},
                current_url="https://auth.openai.com/log-in",
                force_email_otp_login=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(session.post.call_count, 2)
        self.assertTrue(any(call.kwargs.get("force_fresh") for call in ensure.call_args_list))
        sleep.assert_called_once()

    def test_authorize_continue_retry_classification_excludes_account_deactivation(self):
        self.assertTrue(
            codex_oauth._is_transient_authorize_continue_response(
                502,
                '{"error":{"type":"cf_bad_gateway"}}',
            )
        )
        self.assertTrue(
            codex_oauth._is_transient_authorize_continue_response(
                403,
                "<!doctype html><title>Just a moment...</title>",
            )
        )
        self.assertFalse(
            codex_oauth._is_transient_authorize_continue_response(
                403,
                '{"error":{"code":"account_deactivated","message":"Account has been deactivated"}}',
            )
        )

    def test_password_login_uses_password_verify_endpoint(self):
        session = Mock()
        response = Mock(status_code=200)
        session.post.return_value = response

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}), \
             patch("sms_tool.codex_oauth._next_url", return_value="https://auth.openai.com/consent"), \
             patch("sms_tool.codex_oauth._follow_redirects", return_value=(None, "https://auth.openai.com/consent")), \
             patch("sms_tool.codex_oauth._finish_authorization", return_value={"ok": True, "tokens": {"access_token": "at", "refresh_token": "rt_1"}}):
            result = codex_oauth._password_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"password": "Secret!A1"},
                did="did",
                current_url="https://auth.openai.com/log-in/password",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["login_method"], "password")
        self.assertEqual(session.post.call_args.args[0], "https://auth.openai.com/api/accounts/password/verify")
        self.assertEqual(session.post.call_args.kwargs["json"], {"password": "Secret!A1"})

    def test_forced_password_stage_preserves_passwordless_failure(self):
        session = Mock()

        with patch("sms_tool.codex_oauth._detect_protocol_stage", return_value="password"), \
             patch("sms_tool.codex_oauth._password_login_and_exchange", return_value={"ok": False, "error": "password_verify_failed:400"}), \
             patch("sms_tool.codex_oauth._passwordless_login_and_exchange", return_value={"ok": False, "error": "passwordless_email_otp_poll_timeout"}):
            result = codex_oauth._run_protocol_login_stages(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                email="user@example.com",
                data={"email": "user@example.com"},
                did="did",
                current_url="https://auth.openai.com/log-in/password",
                force_email_otp_login=True,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "passwordless_email_otp_poll_timeout")
        self.assertEqual(result["protocol_stage"], "email_otp")
        self.assertEqual(result["fallback_from"], "email_otp_forced")

    def test_account_mfa_does_not_hide_totp_failure_with_mailbox_fallback(self):
        session = Mock()
        password_result = {
            "ok": False,
            "error": "totp_challenge_failed:400",
            "last_url": "https://auth.openai.com/mfa-challenge/test",
        }
        with patch("sms_tool.codex_oauth._detect_protocol_stage", return_value="password"), \
             patch("sms_tool.codex_oauth._password_login_and_exchange", return_value=password_result), \
             patch("sms_tool.codex_oauth._passwordless_login_and_exchange") as passwordless:
            result = codex_oauth._run_protocol_login_stages(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                email="user@example.com",
                data={
                    "email": "user@example.com",
                    "mailbox": {
                        "provider": "account_mfa",
                        "password": "Secret!A1",
                        "totp_secret": "JBSWY3DPEHPK3PXP",
                    },
                },
                did="did",
                current_url="https://auth.openai.com/log-in/password",
            )

        self.assertEqual(result, password_result)
        passwordless.assert_not_called()

    def test_forced_email_otp_preserves_passwordless_failure(self):
        session = Mock()
        phone_attempt = {
            "ok": False,
            "error": "phone_pool_exhausted",
            "message": "all phones exhausted; total remaining capacity=0",
        }

        with patch("sms_tool.codex_oauth._detect_protocol_stage", return_value="password"), \
             patch("sms_tool.codex_oauth._passwordless_login_and_exchange", return_value={
                 "ok": False,
                 "error": "phone_pool_exhausted",
                 "phone_attempt": phone_attempt,
             }):
            result = codex_oauth._run_protocol_login_stages(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                email="user@example.com",
                data={"email": "user@example.com"},
                did="did",
                current_url="https://auth.openai.com/log-in/password",
                force_email_otp_login=True,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "phone_pool_exhausted")
        self.assertEqual(result["protocol_stage"], "email_otp")
        self.assertEqual(result["phone_attempt"], phone_attempt)

    def test_passwordless_send_409_continues_to_mailbox_polling(self):
        response = Mock(status_code=409, text='{"error":"already pending"}')
        session = Mock()
        session.post.return_value = response

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            result = codex_oauth._send_passwordless_otp(session, "did", "https://auth.openai.com/email-verification")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 409)

    def test_email_otp_send_409_tries_legacy_passwordless_fallback(self):
        pending = Mock(status_code=409, text='{"error":"already pending"}')
        accepted = Mock(status_code=200, text="{}")
        session = Mock()
        session.post.side_effect = [pending, accepted]

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            result = codex_oauth._send_passwordless_otp(
                session, "did", "https://auth.openai.com/email-verification"
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["endpoint"], "https://auth.openai.com/api/accounts/passwordless/send-otp")
        self.assertEqual(session.post.call_count, 2)

    def test_passwordless_send_invalid_state_requests_session_restart(self):
        invalid = Mock(
            status_code=409,
            text='{"error":{"message":"Your sign-in session is no longer valid.","code":"invalid_state","redirect_uri":"https://auth.openai.com/log-in"}}',
        )
        session = Mock()
        session.post.return_value = invalid

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            result = codex_oauth._send_passwordless_otp(
                session, "did", "https://auth.openai.com/email-verification"
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "passwordless_send_invalid_state")
        self.assertTrue(result["needs_session_restart"])
        self.assertEqual(session.post.call_count, 1)

    def test_email_otp_requests_use_browser_fetch_headers(self):
        response = Mock(status_code=200, text="{}")
        session = Mock()
        session.post.return_value = response

        with patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            codex_oauth._resend_email_otp(session, "did", "https://auth.openai.com/email-verification")

        headers = session.post.call_args.kwargs["headers"]
        self.assertEqual(headers["accept"], "*/*")
        self.assertEqual(headers["sec-fetch-dest"], "empty")
        self.assertEqual(headers["sec-fetch-mode"], "cors")
        self.assertEqual(headers["sec-fetch-site"], "same-origin")

    def test_passwordless_primes_verification_page_and_sends_before_resend(self):
        page = Mock(status_code=200, url="https://auth.openai.com/email-verification", headers={})
        send = Mock(status_code=200, text='{"success":true}')
        session = Mock()
        session.get.return_value = page
        session.post.return_value = send

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 1}}, clear=False), \
             patch("sms_tool.codex_oauth._poll_email_otp", return_value=None), \
             patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            result = codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "refresh_token": "rt", "token": "cid"}},
                did="did",
                current_url="https://auth.openai.com/email-verification",
                timeout=30,
            )

        self.assertFalse(result["ok"])
        self.assertGreaterEqual(len(session.method_calls), 2)
        self.assertEqual(session.method_calls[0][0], "get")
        self.assertEqual(session.method_calls[1][0], "post")
        self.assertIn("/api/accounts/email-otp/send", session.method_calls[1].args[0])

    def test_authorize_continue_delivery_is_polled_before_any_resend(self):
        page = Mock(status_code=200, url="https://auth.openai.com/email-verification", headers={})
        session = Mock()
        session.get.return_value = page
        poll_issued_after = []

        def poll(*args, **kwargs):
            poll_issued_after.append(kwargs["issued_after_unix"])
            return None

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 1}}, clear=False), \
             patch("sms_tool.codex_oauth._poll_email_otp", side_effect=poll):
            result = codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "refresh_token": "rt", "token": "cid"}},
                did="did",
                current_url="https://auth.openai.com/email-verification",
                timeout=30,
                initial_otp_requested=True,
                otp_issued_after_unix=1234,
            )

        self.assertEqual(poll_issued_after, [1234])
        session.post.assert_not_called()
        self.assertEqual(
            result["otp_delivery"]["attempts"],
            [{"operation": "authorize_continue", "status_code": 200}],
        )

    def test_authorize_continue_on_password_page_explicitly_sends_email_otp(self):
        session = Mock()
        send_result = {"ok": True, "status_code": 200}

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 1}}, clear=False), \
             patch("sms_tool.codex_oauth._send_passwordless_otp", return_value=send_result) as send, \
             patch("sms_tool.codex_oauth._poll_email_otp", return_value=None):
            result = codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "provider": "url_html", "inbox_url": "https://mail.example.test/inbox"}},
                did="did",
                current_url="https://auth.openai.com/log-in/password",
                timeout=30,
                initial_otp_requested=True,
                otp_issued_after_unix=1234,
            )

        send.assert_called_once_with(session, "did", "https://auth.openai.com/log-in/password")
        self.assertEqual(
            result["otp_delivery"]["attempts"],
            [{"operation": "send", "status_code": 200}],
        )

    def test_passwordless_timeout_uses_resend_before_retry(self):
        page = Mock(status_code=200, url="https://auth.openai.com/email-verification", headers={})
        initial_send = Mock(status_code=200, text="{}")
        resend = Mock(status_code=200, text="{}")
        session = Mock()
        session.get.return_value = page
        session.post.side_effect = [initial_send, resend]

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 2}}, clear=False), \
             patch("sms_tool.codex_oauth._poll_email_otp", side_effect=[None, None]), \
             patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            result = codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "refresh_token": "rt", "token": "cid"}},
                did="did",
                current_url="https://auth.openai.com/email-verification",
                timeout=30,
            )

        self.assertFalse(result["ok"])
        post_urls = [call.args[0] for call in session.post.call_args_list]
        self.assertIn("https://auth.openai.com/api/accounts/email-otp/send", post_urls)
        self.assertIn("https://auth.openai.com/api/accounts/email-otp/resend", post_urls)
        self.assertNotIn("https://auth.openai.com/api/accounts/passwordless/send-otp", post_urls)

    def test_passwordless_timeout_budget_is_shared_across_retries(self):
        page = Mock(status_code=200, url="https://auth.openai.com/email-verification", headers={})
        accepted = Mock(status_code=200, text='{"success":true}')
        session = Mock()
        session.get.return_value = page
        session.post.return_value = accepted
        poll_timeouts = []

        def poll(*args, **kwargs):
            poll_timeouts.append(kwargs["timeout"])
            return None

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 3}}, clear=False), \
             patch("sms_tool.codex_oauth._poll_email_otp", side_effect=poll), \
             patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "refresh_token": "rt", "token": "cid"}},
                did="did",
                current_url="https://auth.openai.com/email-verification",
                timeout=60,
            )

        self.assertEqual(poll_timeouts, [20, 20, 20])

    def test_url_mailbox_uses_five_minute_poll_budget_by_default(self):
        page = Mock(status_code=200, url="https://auth.openai.com/email-verification", headers={})
        accepted = Mock(status_code=200, text='{"success":true}')
        session = Mock()
        session.get.return_value = page
        session.post.return_value = accepted
        poll_timeouts = []

        def poll(*args, **kwargs):
            poll_timeouts.append(kwargs["timeout"])
            return None

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 3}}, clear=False), \
             patch("sms_tool.codex_oauth._poll_email_otp", side_effect=poll), \
             patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "provider": "url_html", "inbox_url": "https://mail.example.test/inbox"}},
                did="did",
                current_url="https://auth.openai.com/email-verification",
                timeout=60,
                initial_otp_requested=True,
                otp_issued_after_unix=1234,
            )

        self.assertEqual(poll_timeouts, [100, 100, 100])

    def test_send_accepted_without_new_mail_reports_not_delivered(self):
        page = Mock(status_code=200, url="https://auth.openai.com/email-verification", headers={})
        accepted = Mock(status_code=200, text='{"success":true}')
        session = Mock()
        session.get.return_value = page
        session.post.return_value = accepted

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 2}}, clear=False), \
             patch("sms_tool.codex_oauth._poll_email_otp", side_effect=[None, None]), \
             patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            result = codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "refresh_token": "rt", "token": "cid"}},
                did="did",
                current_url="https://auth.openai.com/email-verification",
                timeout=60,
            )

        self.assertEqual(result["error"], "passwordless_email_otp_not_delivered")
        self.assertEqual(
            result["otp_delivery"]["attempts"],
            [
                {"operation": "send", "status_code": 200},
                {"operation": "resend", "status_code": 200},
            ],
        )

    def test_passwordless_timeout_propagates_invalid_session_restart(self):
        page = Mock(status_code=200, url="https://auth.openai.com/email-verification", headers={})
        invalid = Mock(
            status_code=409,
            text='{"error":{"message":"Your sign-in session is no longer valid.","code":"invalid_state"}}',
        )
        session = Mock()
        session.get.return_value = page
        session.post.return_value = invalid

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 1}}, clear=False), \
             patch("sms_tool.codex_oauth._poll_email_otp", return_value=None), \
             patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            result = codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "refresh_token": "rt", "token": "cid"}},
                did="did",
                current_url="https://auth.openai.com/email-verification",
                timeout=30,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "passwordless_send_invalid_state")
        self.assertTrue(result["needs_session_restart"])

    def test_resend_409_keeps_existing_otp_search_window(self):
        send = Mock(status_code=200, text="{}")
        validate1 = Mock(status_code=400, text='{"error":"bad_code"}')
        resend = Mock(status_code=409, text='{"error":"already pending"}')
        validate2 = Mock(status_code=400, text='{"error":"bad_code"}')
        session = Mock()
        session.get.return_value = Mock(status_code=200, url="https://auth.openai.com/email-verification", headers={})
        session.post.side_effect = [send, validate1, resend, validate2]
        issued_after_values = []

        def poll(*args, **kwargs):
            issued_after_values.append(kwargs.get("issued_after_unix"))
            return "123456"

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 2}}, clear=False), \
             patch("sms_tool.codex_oauth.time.time", side_effect=[1000, 1005]), \
             patch("sms_tool.codex_oauth._poll_email_otp", side_effect=poll), \
             patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            result = codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "refresh_token": "rt", "token": "cid"}},
                did="did",
                current_url="https://auth.openai.com/email-verification",
                timeout=30,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(issued_after_values, [1000, 1000])

    def test_resend_200_keeps_initial_otp_search_window(self):
        send = Mock(status_code=200, text="{}")
        resend = Mock(status_code=200, text='{"success":true}')
        session = Mock()
        session.get.return_value = Mock(status_code=200, url="https://auth.openai.com/email-verification", headers={})
        session.post.side_effect = [send, resend]
        issued_after_values = []

        def poll(*args, **kwargs):
            issued_after_values.append(kwargs.get("issued_after_unix"))
            return None

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 2}}, clear=False), \
             patch("sms_tool.codex_oauth.time.time", side_effect=[1000, 1005]), \
             patch("sms_tool.codex_oauth._poll_email_otp", side_effect=poll), \
             patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "refresh_token": "rt", "token": "cid"}},
                did="did",
                current_url="https://auth.openai.com/email-verification",
                timeout=30,
            )

        self.assertEqual(issued_after_values, [1000, 1000])

    def test_passwordless_pending_timeout_requests_auth_session_restart(self):
        send = Mock(status_code=409, text='{"error":"already pending"}')
        session = Mock()
        session.get.return_value = Mock(status_code=200, url="https://auth.openai.com/email-verification", headers={})
        session.post.return_value = send

        with patch.dict(codex_oauth.CFG, {"email_registration": {"max_otp_retries": 1}}, clear=False), \
             patch("sms_tool.codex_oauth._poll_email_otp", return_value=None), \
             patch("sms_tool.codex_oauth.load_cached_sentinel", return_value={}):
            result = codex_oauth._passwordless_login_and_exchange(
                session=session,
                oauth={"state": "s", "code_verifier": "v", "redirect_uri": "http://localhost"},
                data={"email": "user@example.com", "mailbox": {"email": "user@example.com", "refresh_token": "rt", "token": "cid"}},
                did="did",
                current_url="https://auth.openai.com/email-verification",
                timeout=30,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "passwordless_email_otp_poll_timeout")
        self.assertTrue(result["needs_session_restart"])

    def test_single_phone_oauth_lane_stays_locked_until_token_exchange(self):
        class TrackingLock:
            def __init__(self):
                self.acquired = False

            def __enter__(self):
                self.acquired = True
                return self

            def __exit__(self, exc_type, exc, tb):
                self.acquired = False

        phone_pool = Mock()
        phone_pool.lock = TrackingLock()

        def follow_redirects(*args, **kwargs):
            self.assertTrue(phone_pool.lock.acquired)
            return None, "http://localhost:1455/auth/callback?code=abc&state=s"

        def exchange_callback(*args, **kwargs):
            self.assertTrue(phone_pool.lock.acquired)
            return {"access_token": "at", "refresh_token": "rt"}

        with patch("sms_tool.codex_oauth.complete_phone_verification", return_value={"ok": True, "next_url": "https://auth.openai.com/continue"}), \
             patch("sms_tool.codex_oauth._follow_redirects", side_effect=follow_redirects), \
             patch("sms_tool.codex_oauth._exchange_callback", side_effect=exchange_callback):
            result = codex_oauth._finish_authorization(
                session=Mock(),
                oauth={"state": "s"},
                did="did",
                current_url="https://auth.openai.com/add-phone",
                phone_pool=phone_pool,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tokens"]["refresh_token"], "rt")

    def test_phone_verified_redirect_failure_preserves_phone_attempt(self):
        phone_attempt = {"ok": True, "phone": "+233555123456", "next_url": "https://auth.openai.com/continue"}
        with patch("sms_tool.codex_oauth.complete_phone_verification", return_value=phone_attempt), \
             patch("sms_tool.codex_oauth._follow_redirects", side_effect=RuntimeError("curl52")):
            result = codex_oauth._finish_phone_authorization_locked(
                session=Mock(),
                oauth={"state": "s"},
                did="did",
                current_url="https://auth.openai.com/add-phone",
                phone_pool=Mock(),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["phone_attempt"], phone_attempt)
        self.assertIn("phone_verified_oauth_redirect_failed", result["error"])

    def test_saved_oauth_result_exposes_phone_for_batch_mapping(self):
        phone_attempt = {"ok": True, "phone": "+233555123456", "provider": "smsbower"}
        with TemporaryDirectory() as tmp, \
             patch("sms_tool.codex_oauth.upsert_account") as upsert:
            json_path = f"{tmp}/session.json"
            result = codex_oauth._save_oauth_tokens(
                {"email": "user@example.com"},
                json_path,
                {"access_token": "at", "refresh_token": "rt"},
                "user@example.com",
                "codex_oauth_pkce",
                result={"phone_attempt": phone_attempt},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["phone"], "+233555123456")
        self.assertEqual(result["phone_attempt"], phone_attempt)
        upsert.assert_called_once()

    def test_saved_oauth_result_records_latest_operation_status_without_phone(self):
        with TemporaryDirectory() as tmp, \
             patch("sms_tool.codex_oauth.upsert_account") as upsert:
            json_path = f"{tmp}/session.json"
            codex_oauth._save_oauth_tokens(
                {"email": "user@example.com", "access_token": "old-at"},
                json_path,
                {"access_token": "at", "refresh_token": "rt"},
                "user@example.com",
                "codex_oauth_pkce",
                result={"ok": True, "mode": "codex_oauth_pkce"},
            )

        saved = upsert.call_args.args[0]
        self.assertEqual(saved["response"]["codex_oauth"]["ok"], True)


if __name__ == "__main__":
    unittest.main()
