# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from campusnet.credentials import CredentialStore


@unittest.skipUnless(os.name == "nt", "DPAPI 只在 Windows 上可用")
class TestWindowsCredentials(unittest.TestCase):
    def test_roundtrip_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credential.dat"
            store = CredentialStore(path=path)
            self.assertTrue(store.available)
            store.save("user-1", "secret-pass-测试")
            data = path.read_bytes()
            self.assertNotIn("secret-pass".encode("utf-8"), data)
            self.assertEqual(store.load("user-1"), "secret-pass-测试")
            self.assertIsNone(store.load("other-user"))
            self.assertTrue(store.delete("user-1"))
            self.assertIsNone(store.load("user-1"))


if __name__ == "__main__":
    unittest.main()
