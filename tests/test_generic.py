# -*- coding: utf-8 -*-
import hashlib
import logging
import unittest
from unittest.mock import patch

from campusnet.adapters.generic import GenericAdapter
from campusnet.config import Config
from campusnet.errors import CaptchaRequiredError, CredentialError, UnsupportedPortalError
from campusnet.http import HttpClient
from tests.mock_servers import GenericHandler, GenericState, start_server


def null_logger():
    logger = logging.getLogger("test.generic")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)
    return logger


class TestGenericAdapter(unittest.TestCase):
    def setUp(self):
        self.state = GenericState()
        self.server, self.port = start_server(GenericHandler, self.state)
        self.base = f"http://127.0.0.1:{self.port}"
        self.cfg = Config(
            username=self.state.username,
            password=self.state.password,
            protocol="generic",
            url=self.base + "/login",
            timeout=3,
            verify_tls=False,
            connectivity_urls=[self.base + "/generate_204"],
            log_enabled=False,
        )
        self.http = HttpClient(timeout=3, verify_tls=False)
        self.logger = null_logger()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def make_context(self):
        page = self.http.get_page(self.base + "/login")
        detection = GenericAdapter.detect(page)
        self.assertGreater(detection.score, 0.1)
        context = GenericAdapter.context_from_page(self.cfg, page)
        adapter = GenericAdapter(self.cfg, self.http, self.logger)
        return adapter, adapter.prepare(context)

    def test_plain_login(self):
        adapter, context = self.make_context()
        self.assertEqual(context.extras["encoding"], "plain")
        self.assertFalse(context.extras["captcha"])

        result = adapter.login(context, self.cfg.username, self.cfg.password)
        self.assertTrue(result.ok)
        self.assertTrue(self.state.online)
        self.assertEqual(self.state.login_attempts, 1)
        self.assertEqual(self.state.last_form["csrf"], "token-123")
        self.assertEqual(self.state.last_form["username"], self.state.username)
        self.assertEqual(self.state.last_form["password"], self.state.password)

    def test_wrong_password(self):
        adapter, context = self.make_context()
        self.state.login_attempts = 0
        with patch("campusnet.adapters.generic.time.sleep"):
            with self.assertRaises(CredentialError):
                adapter.login(context, self.cfg.username, "wrong-password")
        self.assertEqual(self.state.login_attempts, 1)
        self.assertFalse(self.state.online)

    def test_md5_password_detection(self):
        self.state.mode = "md5"
        adapter, context = self.make_context()
        self.assertEqual(context.extras["encoding"], "md5")
        result = adapter.login(context, self.cfg.username, self.cfg.password)
        self.assertTrue(result.ok)
        self.assertEqual(
            self.state.last_form["password"],
            hashlib.md5(self.state.password.encode("utf-8")).hexdigest(),
        )

    def test_captcha_is_not_attempted(self):
        self.state.mode = "captcha"
        adapter, context = self.make_context()
        self.assertTrue(context.extras["captcha"])
        self.state.login_attempts = 0
        with self.assertRaises(CaptchaRequiredError):
            adapter.login(context, self.cfg.username, self.cfg.password)
        self.assertEqual(self.state.login_attempts, 0)

    def test_cross_origin_is_rejected(self):
        self.state.mode = "cross"
        adapter, context = self.make_context()
        self.state.login_attempts = 0
        with self.assertRaises(UnsupportedPortalError):
            adapter.login(context, self.cfg.username, self.cfg.password)
        self.assertEqual(self.state.login_attempts, 0)


if __name__ == "__main__":
    unittest.main()
