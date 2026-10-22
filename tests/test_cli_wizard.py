# -*- coding: utf-8 -*-
import contextlib
import io
import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from campusnet import cli, wizard
from campusnet.adapters.srun import SrunAdapter
from campusnet.config import Config, load_config
from campusnet.http import HttpClient
from campusnet.models import PortalCandidate
from tests.mock_servers import SrunHandler, SrunState, start_server


class FakeStore:
    backend = "fake-secure-store"
    available = True

    def __init__(self):
        self.data = {}

    def load(self, username):
        return self.data.get(username)

    def save(self, username, password):
        self.data[username] = password

    def delete(self, username=""):
        if username:
            self.data.pop(username, None)
        else:
            self.data.clear()
        return True


def null_logger(name="test.cli"):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.INFO)
    return logger


class TestCli(unittest.TestCase):
    def setUp(self):
        self.state = SrunState()
        self.server, self.port = start_server(SrunHandler, self.state)
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = HttpClient(timeout=3, verify_tls=False)
        self.logger = null_logger()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def config(self, password="test-pass"):
        return Config(
            username=self.state.username,
            password=password,
            protocol="srun",
            url=self.base,
            hosts=[self.base],
            timeout=3,
            verify_tls=False,
            connectivity_urls=[self.base + "/generate_204"],
            log_enabled=False,
        )

    def test_connect_once_and_status(self):
        cfg = self.config()
        store = FakeStore()
        self.assertEqual(cli.run_status(cfg, self.http, self.logger), 2)
        code = cli.connect_once(cfg, self.http, self.logger, store)
        self.assertEqual(code, 0)
        self.assertTrue(self.state.online)
        # 已在线分支必须直接返回，不能调用登录或崩溃。
        self.assertEqual(cli.connect_once(cfg, self.http, self.logger, store), 0)
        self.assertEqual(cli.run_status(cfg, self.http, self.logger), 0)

    def test_wrong_password_returns_3_once(self):
        cfg = self.config(password="bad-password")
        store = FakeStore()
        code = cli.connect_once(cfg, self.http, self.logger, store)
        self.assertEqual(code, 3)
        self.assertEqual(self.state.login_attempts, 1)
        self.assertFalse(self.state.online)

    def test_stored_password_is_used(self):
        cfg = self.config(password="")
        store = FakeStore()
        store.save(cfg.username, "test-pass")
        code = cli.connect_once(cfg, self.http, self.logger, store)
        self.assertEqual(code, 0)
        self.assertTrue(self.state.online)


class TestWizard(unittest.TestCase):
    def setUp(self):
        self.state = SrunState()
        self.server, self.port = start_server(SrunHandler, self.state)
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = HttpClient(timeout=3, verify_tls=False)
        self.logger = null_logger("test.wizard")
        page = self.http.get_page(self.base + "/srun_portal_pc")
        detection = SrunAdapter.detect(page)
        self.candidate = PortalCandidate(page=page, detection=detection, source="测试")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def run_wizard(self, password_values, inputs):
        tmp = tempfile.mkdtemp(prefix="campusnet_wizard_")
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg_path = Path(tmp) / "config.ini"
        cfg = Config(
            username=self.state.username,
            protocol="auto",
            url="",
            timeout=3,
            verify_tls=False,
            connectivity_urls=[self.base + "/generate_204"],
            log_enabled=False,
        )
        store = FakeStore()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with patch("campusnet.wizard.discover_candidates", return_value=[self.candidate]):
                with patch("campusnet.wizard.is_interactive", return_value=True):
                    with patch("builtins.input", side_effect=inputs):
                        with patch(
                            "campusnet.wizard.getpass.getpass",
                            side_effect=password_values,
                        ):
                            code = wizard.run_setup_wizard(
                                cfg, cfg_path, self.http, self.logger, store
                            )
        return code, cfg_path, store, output.getvalue()

    def test_wizard_success(self):
        code, cfg_path, store, output = self.run_wizard(
            ["test-pass", "test-pass"], ["", "", "", ""]
        )
        self.assertEqual(code, 0, output)
        self.assertTrue(self.state.online)
        self.assertEqual(store.load(self.state.username), "test-pass")
        loaded = load_config(cfg_path)
        self.assertEqual(loaded.protocol, "srun")
        self.assertEqual(loaded.url, self.base)
        self.assertEqual(loaded.username, self.state.username)
        self.assertIn(self.base, loaded.hosts)
        self.assertEqual(loaded.ac_id, "1")

    def test_wizard_wrong_password_deletes_saved_credential(self):
        self.state.login_attempts = 0
        code, cfg_path, store, output = self.run_wizard(
            ["wrong-pass", "wrong-pass"], ["", "", "", ""]
        )
        self.assertEqual(code, 3, output)
        self.assertIsNone(store.load(self.state.username))
        self.assertEqual(self.state.login_attempts, 1)
        self.assertTrue(cfg_path.exists())

    def test_prompt_eof_uses_default_instead_of_looping(self):
        with patch("builtins.input", side_effect=EOFError):
            self.assertEqual(wizard.prompt("请选择", "1"), "1")


if __name__ == "__main__":
    unittest.main()
