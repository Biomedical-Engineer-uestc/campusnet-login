# -*- coding: utf-8 -*-
"""其它 Wi-Fi / 重定向边界的测试。"""

import logging
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from campusnet import cli
from campusnet.config import Config
from campusnet.errors import NetworkError
from campusnet.http import HttpClient
from tests.mock_servers import (
    GenericHandler,
    GenericState,
    SrunHandler,
    SrunState,
    start_server,
)


def null_logger(name="test.boundary"):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.INFO)
    return logger


class _RedirectHandler(BaseHTTPRequestHandler):
    target = ""

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", self.target)
        self.end_headers()


class TestBoundaries(unittest.TestCase):
    def setUp(self):
        self.srun_state = SrunState()
        self.srun_server, self.srun_port = start_server(
            SrunHandler, self.srun_state
        )
        self.srun_base = f"http://127.0.0.1:{self.srun_port}"
        self.http = HttpClient(timeout=3, verify_tls=False)
        self.logger = null_logger()

    def tearDown(self):
        self.srun_server.shutdown()
        self.srun_server.server_close()

    def test_quick_status_does_not_touch_school_portal_when_internet_is_up(self):
        generic_state = GenericState()
        generic_state.online = True
        generic_server, generic_port = start_server(
            GenericHandler, generic_state
        )
        self.addCleanup(generic_server.shutdown)
        self.addCleanup(generic_server.server_close)

        check_url = f"http://127.0.0.1:{generic_port}/generate_204"
        cfg = Config(
            username="test-user",
            protocol="srun",
            url=self.srun_base,
            hosts=[self.srun_base],
            connectivity_urls=[check_url],
            timeout=3,
            verify_tls=False,
            log_enabled=False,
        )
        runtime, status = cli.check_status(
            cfg, self.http, self.logger, quick=True
        )
        self.assertTrue(status.online)
        self.assertTrue(status.info["connectivity_only"])
        self.assertEqual(self.srun_state.status_requests, 0)

    def test_redirect_to_other_host_is_rejected(self):
        _RedirectHandler.target = (
            f"http://localhost:{self.srun_port}/srun_portal_pc"
        )
        redirect_server = ThreadingHTTPServer(
            ("127.0.0.1", 0), _RedirectHandler
        )
        threading.Thread(
            target=redirect_server.serve_forever, daemon=True
        ).start()
        self.addCleanup(redirect_server.shutdown)
        self.addCleanup(redirect_server.server_close)
        redirect_base = f"http://127.0.0.1:{redirect_server.server_address[1]}"

        cfg = Config(
            username="test-user",
            protocol="srun",
            url=redirect_base,
            hosts=[redirect_base],
            timeout=3,
            verify_tls=False,
            log_enabled=False,
        )
        with self.assertRaises(NetworkError):
            cli.build_runtime(cfg, self.http, self.logger)
        # build_runtime 只拉取登录页，不应尝试提交密码
        self.assertEqual(self.srun_state.login_attempts, 0)


if __name__ == "__main__":
    unittest.main()
