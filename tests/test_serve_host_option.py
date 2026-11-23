"""The serve command binds to an explicitly selected host."""
import unittest
from unittest.mock import patch

from textfold import server


class ServeHostOption(unittest.TestCase):
    def test_serve_binds_to_given_host(self):
        with patch.object(server.store, "initialize_storage"), \
             patch.object(server.agents, "scan"), \
             patch.object(server.store, "empty_trash"), \
             patch.object(server, "ThreadingHTTPServer") as http_server, \
             patch.object(server, "announce", return_value="server.json"), \
             patch.object(server, "withdraw"), \
             patch.object(server.threading, "Thread"), \
             patch.object(server, "signal") as signals:
            http_server.return_value.server_address = ("0.0.0.0", 3017)
            server.serve(3017, host="0.0.0.0")

        self.assertEqual(http_server.call_args.args[0], ("0.0.0.0", 3017))
        http_server.return_value.serve_forever.assert_called_once()


if __name__ == "__main__":
    unittest.main()
