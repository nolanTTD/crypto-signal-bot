import os
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from service import Handler, prerequisites


class ServiceTests(unittest.TestCase):
    def test_collection_blocked_without_durable_storage(self):
        with patch.dict(os.environ, {'HELIUS_API_KEY': 'secret', 'WATCH_WALLETS': 'address'}, clear=True):
            with patch('service.os.path.ismount', return_value=False):
                self.assertIn('PERSISTENT_DISK_AT_/var/data', prerequisites()[0])

    def test_public_health_and_protected_status(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:'+str(server.server_port)
        try:
            with patch.dict(os.environ, {'STATUS_TOKEN': 'secret'}):
                with urlopen(base+'/health') as r:
                    self.assertNotIn(b'secret', r.read())
                with self.assertRaises(HTTPError) as r:
                    urlopen(base+'/status')
                self.assertEqual(r.exception.code, 401)
                with urlopen(Request(base+'/status', headers={'Authorization':'Bearer secret'})) as r:
                    self.assertIn(b'missing_configuration', r.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
