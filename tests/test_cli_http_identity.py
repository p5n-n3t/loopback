import unittest
from unittest.mock import patch
from helpers import RUNTIME  # noqa: F401
import cli

class HTTPIdentityTests(unittest.TestCase):
    def test_health_probe_identifies_client_and_preserves_explicit_headers(self):
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value.status = 200
            cli.http('https://gateway.example/healthz')
            request = build.return_value.open.call_args.args[0]
            self.assertTrue(request.get_header('User-agent').startswith('loopback-cli/'))
            cli.http('https://gateway.example/healthz', headers={'User-Agent':'explicit-client'})
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('User-agent'),'explicit-client')
