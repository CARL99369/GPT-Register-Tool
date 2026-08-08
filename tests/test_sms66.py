import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from sms_tool import phone_reuse
from sms_tool.sms66 import Sms66Activation, Sms66Client, extract_sms_code, normalize_phone


class Sms66ClientTests(unittest.TestCase):
    def test_buy_phone_posts_project_and_country(self):
        response = Mock()
        response.json.return_value = {
            "sta": "ok",
            "data": {"order_id": "order-1", "phones": ["14155550123"]},
        }
        response.raise_for_status.return_value = None
        with patch("sms_tool.sms66._requests.post", return_value=response) as request:
            activation = Sms66Client(api_key="key").buy_number(app_id="480", country_id="1")

        self.assertEqual(activation.order_id, "order-1")
        self.assertEqual(activation.phone, "+14155550123")
        self.assertEqual(request.call_args.args[0], "https://app.yuntl.cc/api/buy_phone")
        self.assertEqual(request.call_args.kwargs["data"]["app_id"], "480")
        self.assertEqual(request.call_args.kwargs["data"]["country_id"], "1")

    def test_get_sms_extracts_code_from_documented_shape(self):
        response = Mock()
        response.json.return_value = {
            "sta": "ok",
            "data": [{"phone": "14155550123", "sms_content": "OpenAI code: 522477"}],
        }
        response.raise_for_status.return_value = None
        with patch("sms_tool.sms66._requests.get", return_value=response):
            rows = Sms66Client(api_key="key").get_sms("480", "+14155550123")
        self.assertEqual(rows[0]["phone"], "14155550123")
        self.assertEqual(extract_sms_code(rows[0]["sms_content"]), "522477")

    def test_available_numbers_and_designated_purchase(self):
        available_response = Mock()
        available_response.json.return_value = {
            "sta": "ok",
            "data": {
                "total": 2,
                "list": [
                    {"phone": "12025550101", "expiration_date": "2026-09-01 00:00:00"},
                    {"phone": "14155550123", "expiration_date": "2026-09-02 00:00:00"},
                ],
            },
        }
        available_response.raise_for_status.return_value = None
        buy_response = Mock()
        buy_response.json.return_value = {
            "sta": "ok",
            "data": {"order_id": "vip-order-1", "phones": ["14155550123"], "failed_phones": []},
        }
        buy_response.raise_for_status.return_value = None
        client = Sms66Client(api_key="key")

        with patch("sms_tool.sms66._requests.get", return_value=available_response), \
             patch("sms_tool.sms66._requests.post", return_value=buy_response) as request:
            numbers = client.get_available_numbers("480")
            activation = client.buy_designated_number("480", "+14155550123")

        self.assertEqual(numbers[1]["phone"], "+14155550123")
        self.assertEqual(activation.order_id, "vip-order-1")
        self.assertEqual(request.call_args.args[0], "https://app.yuntl.cc/api/buy_designated_phone")
        self.assertEqual(request.call_args.kwargs["data"]["phones"], "14155550123")

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone("0014155550123"), "+14155550123")

    def test_phone_pool_uses_sms66_project_480(self):
        with TemporaryDirectory() as temp, patch.dict(phone_reuse.CFG, {
            "phone_reuse": {
                "source": "sms66",
                "state_file": f"{temp}/state.json",
                "smsbower": {"api_key": "other-provider-key"},
                "sms66": {
                    "api_key": "key",
                    "project_id": "480",
                    "country_id": "1",
                    "max_reuse_count": 5,
                },
            },
        }, clear=False):
            pool = phone_reuse.create_phone_pool()

        self.assertEqual(len(pool.phones), 1)
        self.assertEqual(pool.phones[0].provider, "sms66")
        self.assertEqual(pool.phones[0].project_id, "480")
        self.assertEqual(pool.phones[0].country, "1")
        self.assertEqual(pool.phones[0].max_reuse_count, 5)

    def test_sms66_acquire_uses_selected_designated_phone(self):
        slot = phone_reuse.PhoneSlot(
            phone="",
            provider="sms66",
            api_key="key",
            project_id="480",
            country="1",
            designated_phone="+14155550123",
        )
        client = Mock()
        client.buy_designated_number.return_value = Sms66Activation(
            "vip-order-1", "+14155550123", "480", ""
        )

        with patch("sms_tool.phone_reuse._sms66_client", return_value=client):
            prepared = phone_reuse._acquire_sms66_number(slot)

        self.assertTrue(prepared)
        client.buy_designated_number.assert_called_once_with("480", "+14155550123")
        self.assertEqual(slot.activation_id, "vip-order-1")


if __name__ == "__main__":
    unittest.main()
