import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from sms_tool import paypal_auto


class SmsPollRedactionTests(unittest.TestCase):
    def test_poll_error_does_not_print_secret_url(self):
        secret_url = "http://sms66.vip/apisms/private-token"
        output = io.StringIO()

        with (
            patch.object(
                paypal_auto._requests,
                "get",
                side_effect=RuntimeError("request failed for " + secret_url),
            ),
            patch.object(paypal_auto.time, "time", side_effect=[0, 0, 1, 1]),
            patch.object(paypal_auto.time, "sleep"),
            redirect_stdout(output),
        ):
            code = paypal_auto._poll_sms_code(
                secret_url,
                {"raw": ""},
                timeout=1,
                poll_interval=1,
            )

        self.assertIsNone(code)
        self.assertNotIn("private-token", output.getvalue())
        self.assertIn("RuntimeError", output.getvalue())


if __name__ == "__main__":
    unittest.main()
