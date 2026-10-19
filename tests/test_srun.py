# -*- coding: utf-8 -*-
import logging
import unittest

from campusnet.adapters.srun import SrunAdapter, build_auth_payload
from campusnet.config import Config
from campusnet.errors import CredentialError
from campusnet.http import HttpClient
from tests.mock_servers import SrunState, SrunHandler, start_server


def null_logger():
    logger = logging.getLogger("test.srun")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)
    return logger


class TestSrunAdapter(unittest.TestCase):
    def setUp(self):
        self.state = SrunState()
        self.server, self.port = start_server(SrunHandler, self.state)
        self.base = f"http://127.0.0.1:{self.port}"
        self.cfg = Config(
            username=self.state.username,
            password=self.state.password,
            protocol="srun",
            url=self.base + "/srun_portal_pc",
            hosts=[self.base],
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
        page = self.http.get_page(self.base + "/srun_portal_pc")
        detection = SrunAdapter.detect(page)
        self.assertGreaterEqual(detection.score, 0.9)
        context = SrunAdapter.context_from_page(self.cfg, page)
        adapter = SrunAdapter(self.cfg, self.http, self.logger)
        return adapter, adapter.prepare(context)

    def test_protocol_vector(self):
        hmd5, info, chksum = build_auth_payload(
            "test-user",
            "Test Pass!@#汉字",
            "192.0.2.10",
            "1",
            "6e5d28903bedab24abdacc633fa336c05b7d7fb0b717629c34ca410fccf1657a",
        )
        self.assertEqual(hmd5, "5aeb01e05493ffad34e94d97538d56a6")
        self.assertEqual(
            info,
            "{SRBX1}jU1bq7xhdbRTgJFEXNNevlG+367R4PD3Jq0CGBGD7EpyGqS/D6yMLHqgrustUc7eBQso0TT6/XsDKjuDG3dPMS0kUylFIKsuSjoko0d/+gPVlFpxIU5L7ZeCjZJsJtv7mF4C8Ah+uFenpE0C",
        )
        self.assertEqual(chksum, "4946ba5ad3f0c7bc89d9432c14185abb57c8b559")

    def test_status_and_login(self):
        adapter, context = self.make_context()
        status = adapter.status(context)
        self.assertFalse(status.online)

        result = adapter.login(context, self.cfg.username, self.cfg.password)
        self.assertTrue(result.ok)
        self.assertTrue(self.state.online)
        self.assertEqual(self.state.login_attempts, 1)

        status = adapter.status(context)
        self.assertTrue(status.online)
        self.assertEqual(status.info["user_name"], self.state.username)

        verified = adapter.verify(context, self.cfg.username)
        self.assertIsNotNone(verified)

    def test_wrong_password_single_attempt(self):
        adapter, context = self.make_context()
        self.state.login_attempts = 0
        with self.assertRaises(CredentialError):
            adapter.login(context, self.cfg.username, "wrong-password")
        self.assertEqual(self.state.login_attempts, 1)
        self.assertFalse(self.state.online)


if __name__ == "__main__":
    unittest.main()
