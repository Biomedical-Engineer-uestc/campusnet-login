# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from campusnet.config import (
    DEFAULT_CONFIG_TEMPLATE,
    Config,
    load_config,
    save_config,
)


class TestConfig(unittest.TestCase):
    def test_v1_config_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                """[account]
username = olduser
password =
remember_password = true

[portal]
hosts = https://portal.example.edu, http://10.0.0.2
ac_id = 1
theme = pro
timeout = 8
verify_tls = true

[watch]
interval = 30
log = true
""",
                encoding="utf-8",
            )
            cfg = load_config(path)
            self.assertEqual(cfg.protocol, "srun")
            self.assertEqual(cfg.url, "https://portal.example.edu")
            self.assertEqual(
                cfg.hosts, ["https://portal.example.edu", "http://10.0.0.2"]
            )
            self.assertEqual(cfg.username, "olduser")
            self.assertTrue(cfg.is_configured())

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            cfg = Config(
                username="u",
                protocol="generic",
                url="http://portal.example/login",
                password="",
                hosts=["http://portal.example"],
                service="svc",
                password_encoding="md5",
                username_field="user",
                password_field="pwd",
                login_url="http://portal.example/do_login",
                extra_fields={"a": "b", "c": "d"},
                success_keywords=["成功"],
                connectivity_urls=["http://check.example/204"],
                timeout=5,
                verify_tls=False,
                allow_cross_origin=True,
                interval=15,
                log_enabled=False,
            )
            save_config(path, cfg)
            loaded = load_config(path)
            self.assertEqual(loaded.username, "u")
            self.assertEqual(loaded.protocol, "generic")
            self.assertEqual(loaded.url, cfg.url)
            self.assertEqual(loaded.password, "")
            self.assertEqual(loaded.extra_fields, {"a": "b", "c": "d"})
            self.assertEqual(loaded.connectivity_urls, ["http://check.example/204"])
            self.assertEqual(loaded.interval, 15)
            self.assertFalse(loaded.log_enabled)
            self.assertTrue(loaded.allow_cross_origin)
            self.assertEqual(loaded.password_encoding, "md5")

    def test_template_has_no_personal_account(self):
        self.assertNotRegex(DEFAULT_CONFIG_TEMPLATE, r"\b\d{10,12}\b")
        self.assertIn("protocol = auto", DEFAULT_CONFIG_TEMPLATE)
        self.assertIn("username =", DEFAULT_CONFIG_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
