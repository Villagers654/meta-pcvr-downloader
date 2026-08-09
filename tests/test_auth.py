from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meta_pcvr_downloader.auth import get_access_token


class FirefoxAuthenticationTests(unittest.TestCase):
    def _create_profile(self, home: Path, relative_root: str) -> Path:
        database = home / relative_root / "test.default-release/cookies.sqlite"
        database.parent.mkdir(parents=True)
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "create table moz_cookies (host text, name text, value text, expiry integer)"
            )
            connection.execute(
                "insert into moz_cookies values (?, ?, ?, ?)",
                (".meta.com", "oc_ac_at", "firefox-test-token", 2_000_000_000),
            )
            connection.commit()
        finally:
            connection.close()
        return database

    def test_native_firefox_profile(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(os.environ, {"META_OCULUS_ACCESS_TOKEN": ""}),
        ):
            home = Path(directory)
            self._create_profile(home, ".mozilla/firefox")
            self.assertEqual(get_access_token(home), "firefox-test-token")

    def test_flatpak_firefox_profile(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(os.environ, {"META_OCULUS_ACCESS_TOKEN": ""}),
        ):
            home = Path(directory)
            self._create_profile(home, ".var/app/org.mozilla.firefox/.mozilla/firefox")
            self.assertEqual(get_access_token(home), "firefox-test-token")

    def test_snap_firefox_profile(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(os.environ, {"META_OCULUS_ACCESS_TOKEN": ""}),
        ):
            home = Path(directory)
            self._create_profile(home, "snap/firefox/common/.mozilla/firefox")
            self.assertEqual(get_access_token(home), "firefox-test-token")
