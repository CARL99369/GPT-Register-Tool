from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlsplit

from standalone_core.card_payment import (
    CardPaymentConfig,
    CardPaymentError,
    run_card_payment,
)


class FakeResponse:
    def __init__(self, payload=None, status_code: int = 200):
        self.status_code = status_code
        self.payload = payload if payload is not None else {}
        self.text = json.dumps(self.payload)

    def json(self):
        return self.payload


class PaymentContractSession:
    """Offline response fixture for the captured bind -> pay API contract."""

    def __init__(self, *, final_status: str = "succeeded", plan_type: str = "plus"):
        self.final_status = final_status
        self.plan_type = plan_type
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def _record(self, method: str, url: str) -> str:
        path = urlsplit(url).path
        self.calls.append((method, path))
        return path

    def get(self, url, **_kwargs):
        path = self._record("GET", url)
        if path == "/backend-api/payments/payment_methods":
            return FakeResponse({"data": [{"id": "pm_browser_fixture"}]})
        if path == "/v1/payment_methods":
            return FakeResponse({"data": [{"id": "pm_browser_fixture"}]})
        if path == "/backend-api/subscriptions":
            return FakeResponse({"plan_type": self.plan_type})
        return FakeResponse({"ok": True})

    def post(self, url, *, data=None, json=None, **_kwargs):
        path = self._record("POST", url)
        if path == "/backend-api/payments/payment_method":
            return FakeResponse(
                {
                    "id": "seti_bind_fixture",
                    "client_secret": "seti_bind_fixture_secret_value",
                }
            )
        if path == "/v1/setup_intents/seti_bind_fixture/confirm":
            form = parse_qs(data or "")
            if form.get("payment_method") != ["pm_browser_fixture"]:
                return FakeResponse({"error": {"message": "wrong payment method"}}, 400)
            return FakeResponse(
                {
                    "id": "seti_bind_fixture",
                    "status": "succeeded",
                    "payment_method": "pm_browser_fixture",
                    "customer": "cus_fixture",
                }
            )
        if path == "/backend-api/payments/checkout/taxes":
            return FakeResponse({"ok": True})
        if path == "/v1/confirmation_tokens":
            return FakeResponse({"id": "ctoken_fixture"})
        if path == "/backend-api/payments/checkout/confirm":
            return FakeResponse(
                {
                    "id": "seti_final_fixture",
                    "client_secret": "seti_final_fixture_secret_value",
                }
            )
        if path == "/v1/setup_intents/seti_final_fixture/confirm":
            return FakeResponse(
                {"id": "seti_final_fixture", "status": self.final_status}
            )
        return FakeResponse({"ok": True})

    def close(self):
        self.closed = True


def config() -> CardPaymentConfig:
    return CardPaymentConfig(
        token="fixture-token",
        account_id="account_fixture",
        country="PH",
        currency="PHP",
        billing={
            "name": "Fixture Holder",
            "line1": "1 Fixture Street",
            "city": "Wilmington",
            "state": "DE",
            "postal_code": "19801",
            "country": "US",
        },
        payment_method_id="pm_browser_fixture",
        card_last4="4242",
        checkout_id="oaics_first_fixture",
        processor_entity="openai_llc",
        publishable_key="pk_test_fixture",
        strong_bind_direct=True,
        fast_verify=True,
    )


def refreshed_checkout():
    return {
        "checkout_session_id": "oaics_second_fixture",
        "processor_entity": "openai_llc",
        "currency": "PHP",
        "amount": "0",
        "_publishable_key": "pk_test_fixture",
    }


class PaymentContractTests(unittest.TestCase):
    def test_full_payment_contract_succeeds_offline_without_a_card(self):
        session = PaymentContractSession()
        result = run_card_payment(
            config(),
            session_factory=lambda _config, _proxy: session,
            refresh_checkout=refreshed_checkout,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["setup_status"], "succeeded")
        self.assertEqual(result["subscription_plan"], "plus")
        self.assertTrue(session.closed)
        self.assertIn(
            ("POST", "/backend-api/payments/checkout/confirm"), session.calls
        )
        self.assertIn(
            ("POST", "/v1/setup_intents/seti_final_fixture/confirm"), session.calls
        )

    def test_http_200_requires_action_is_not_reported_as_paid(self):
        session = PaymentContractSession(final_status="requires_action")
        with self.assertRaisesRegex(CardPaymentError, "requires_action"):
            run_card_payment(
                config(),
                session_factory=lambda _config, _proxy: session,
                refresh_checkout=refreshed_checkout,
            )

    def test_http_200_with_missing_plan_is_not_reported_as_paid(self):
        session = PaymentContractSession(plan_type="")
        with self.assertRaisesRegex(CardPaymentError, "Plus plan was not activated"):
            run_card_payment(
                config(),
                session_factory=lambda _config, _proxy: session,
                refresh_checkout=refreshed_checkout,
            )


if __name__ == "__main__":
    unittest.main()
