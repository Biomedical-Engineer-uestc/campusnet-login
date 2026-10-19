# -*- coding: utf-8 -*-
import logging
import unittest

from campusnet.adapters.ruijie import RuijieAdapter
from campusnet.config import Config
from campusnet.errors import CredentialError, UnsupportedPortalError
from campusnet.http import HttpClient
from tests.mock_servers import RuijieHandler, RuijieState, start_server


def null_logger():
    logger = logging.getLogger("test.ruijie")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)
    return logger


class TestRuijieAdapter(unittest.TestCase):
    def setUp(self):
        self.state = RuijieState()
        self.server, self.port = start_server(RuijieHandler, self.state)
        self.base = f"http://127.0.0.1:{self.port}"
        self.cfg = Config(
            username=self.state.username,
            password=self.state.password,
            protocol="ruijie",
            url=self.base + "/eportal/index.jsp",
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

    def make_context(self, page_url=None):
        page = self.http.get_page(page_url or self.base + "/eportal/index.jsp")
        detection = RuijieAdapter.detect(page)
        self.assertGreaterEqual(detection.score, 0.8)
        context = RuijieAdapter.context_from_page(self.cfg, page)
        adapter = RuijieAdapter(self.cfg, self.http, self.logger)
        return adapter, adapter.prepare(context)

    def test_login(self):
        adapter, context = self.make_context()
        self.assertEqual(context.extras["service"], "默认服务")
        self.assertEqual(context.extras["query_string"], self.state.query_string)
        self.assertFalse(context.extras["encryption_required"])

        result = adapter.login(context, self.cfg.username, self.cfg.password)
        self.assertTrue(result.ok)
        self.assertTrue(self.state.online)
        self.assertEqual(self.state.login_attempts, 1)
        self.assertEqual(self.state.last_form["userId"], self.state.username)
        self.assertEqual(self.state.last_form["password"], self.state.password)
        self.assertEqual(self.state.last_form["passwordEncrypt"], "false")

    def test_wrong_password_single_attempt(self):
        adapter, context = self.make_context()
        self.state.login_attempts = 0
        with self.assertRaises(CredentialError):
            adapter.login(context, self.cfg.username, "bad-password")
        self.assertEqual(self.state.login_attempts, 1)
        self.assertFalse(self.state.online)

    def test_encryption_is_detected_without_sending_password(self):
        self.state.encryption_required = True
        adapter, context = self.make_context()
        self.assertTrue(context.extras["encryption_required"])
        self.state.login_attempts = 0
        with self.assertRaises(UnsupportedPortalError):
            adapter.login(context, self.cfg.username, self.cfg.password)
        self.assertEqual(self.state.login_attempts, 0)


if __name__ == "__main__":
    unittest.main()
