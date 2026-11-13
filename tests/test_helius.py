import io
import sqlite3
import unittest
from urllib.error import HTTPError
from helius_feed import history, collect


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')

    def tearDown(self):
        self.db.close()

    def test_missing_key_and_invalid_wallet(self):
        for address, key in [('bad', 'secret'), ('1'*32, '')]:
            with self.assertRaises(ValueError):
                history(address, key)

    def test_error_does_not_expose_key(self):
        def fail(*args, **kw):
            raise HTTPError('https://host/?api-key=SECRET', 429, 'SECRET', {}, None)
        with self.assertRaises(RuntimeError) as r:
            history('1'*32, 'SECRET', opener=fail)
        self.assertNotIn('SECRET', str(r.exception))

    def test_duplicate_fetch_and_cursor(self):
        def fetch(wallet, key, before):
            return [{'signature': 'one', 'type': 'SWAP'}] if before is None else []
        a = collect(self.db, 'wallet', 'secret', fetch=fetch)
        b = collect(self.db, 'wallet', 'secret', fetch=fetch)
        self.assertEqual(a['new_transactions'], 1)
        self.assertEqual(b['new_transactions'], 0)
        self.assertTrue(b['caught_up'])

    def test_page_cap_does_not_skip_gap(self):
        self.test_duplicate_fetch_and_cursor()
        def fetch(wallet, key, before):
            return [{'signature': 'new'}]
        r = collect(self.db, 'wallet', 'secret', max_pages=1, fetch=fetch)
        self.assertFalse(r['caught_up'])
        self.assertEqual(self.db.execute('SELECT head FROM helius_cursor').fetchone()[0], 'one')

    def test_response_validation(self):
        def invalid(*a, **k):
            return io.BytesIO(b'{"error":"denied"}')
        with self.assertRaises(RuntimeError):
            history('1'*32, 'secret', opener=invalid)
