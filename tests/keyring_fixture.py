"""Explicit test-only keyring, isolated by TEXTFOLD_TEST_KEYRING_FILE."""
import json
import os
from pathlib import Path
from keyring.backend import KeyringBackend


class FixtureKeyring(KeyringBackend):
    priority = 1

    def _path(self):
        return Path(os.environ['TEXTFOLD_TEST_KEYRING_FILE'])

    def _load(self):
        path = self._path()
        return json.loads(path.read_text()) if path.exists() else {}

    def get_password(self, service, username):
        return self._load().get(service + '/' + username)

    def set_password(self, service, username, password):
        values = self._load()
        values[service + '/' + username] = password
        self._path().write_text(json.dumps(values))

    def delete_password(self, service, username):
        values = self._load()
        values.pop(service + '/' + username, None)
        self._path().write_text(json.dumps(values))
