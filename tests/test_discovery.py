import json
import sqlite3
import unittest
from discovery import SOL, USDC, discover, decode, screen
from helius_feed import collect

W = 'A'*32
T = 'B'*32


def tx(sig='one', side='buy'):
    return {'signature': sig, 'type': 'SWAP', 'feePayer': W, 'timestamp': 1000,
            'tokenTransfers':[
                {'mint': USDC, 'tokenAmount': 100,
                 'fromUserAccount': W if side=='buy' else T,
                 'toUserAccount': T if side=='buy' else W},
                {'mint': SOL, 'tokenAmount': 1,
                 'fromUserAccount': T if side=='buy' else W,
                 'toUserAccount': W if side=='buy' else T}]}


class DiscoveryTests(unittest.TestCase):
    def test_trade_legs(self):
        f, reason = decode(tx(), W)
        self.assertIsNone(reason)
        self.assertEqual(f['side'], 'buy')
        self.assertEqual(f['quote_amount'], 100)
        self.assertEqual(decode(tx(side='sell'), W)[0]['side'], 'sell')

    def test_transfer_failed_and_extra_legs(self):
        a=tx(); a['type']='TRANSFER'
        b=tx(); b['transactionError']={'error':'failure'}
        c=tx(); c['tokenTransfers'].append(dict(c['tokenTransfers'][0]))
        for r in [a,b,c]: self.assertIsNone(decode(r,W)[0])

    def test_unknown_owner_nan_native_legs(self):
        a=tx(); a['feePayer']=T
        b=tx(); b['tokenTransfers'][0]['tokenAmount']='NaN'
        c=tx(); c['nativeTransfers']=[{'fromUserAccount':W,'toUserAccount':T}]
        for r in [a,b,c]: self.assertIsNone(decode(r,W)[0])

    def test_discovery_budget_and_dedup(self):
        db=sqlite3.connect(':memory:'); calls=[]
        def fetch(seed,key):
            calls.append(seed)
            return [tx(), tx()]
        self.assertEqual(discover(db,'unused',fetch,1000), [W])
        self.assertEqual(discover(db,'unused',fetch,1001), [W])
        self.assertEqual(len(calls),2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM discovery_evidence').fetchone()[0],2)
        db.close()

    def test_partial_history_never_qualified(self):
        db=sqlite3.connect(':memory:')
        def fetch(*args): return [tx('sold','sell')] if args[2] is None else []
        collect(db,W,'unused',fetch=fetch)
        r=screen(db,[W])[0]
        self.assertEqual(r['status'],'research_candidate_only')
        self.assertIn('unknown_cost_basis',r['qualification_blocks'])
        self.assertEqual(r['sample_realized_gross_usdc'],0)
        db.close()

    def test_errors_report_codes_only_and_do_not_retry_unbounded(self):
        db=sqlite3.connect(':memory:'); calls=[]
        def fetch(*args):
            calls.append(1)
            raise RuntimeError('Helius HTTP 401; request URL redacted')
        discover(db,'SECRET',fetch,1000)
        discover(db,'SECRET',fetch,1001)
        meta=dict(db.execute('SELECT key,value FROM discovery_meta'))
        self.assertEqual(len(calls),2)
        self.assertEqual(meta['request_error_codes'],'["HTTP_401", "HTTP_401"]')
        self.assertNotIn('SECRET',json.dumps(meta))
        db.close()

    def test_changed_key_retries_once_without_exposing_fingerprint(self):
        db=sqlite3.connect(':memory:'); calls=[]
        def fetch(*args):
            calls.append(1)
            return []
        discover(db,'old-key',fetch,1000)
        discover(db,'new-key',fetch,1001)
        discover(db,'new-key',fetch,1002)
        self.assertEqual(len(calls),4)
        meta=dict(db.execute('SELECT key,value FROM discovery_meta'))
        self.assertNotIn('fingerprint',str(meta))
        self.assertNotIn('new-key',str(meta))
        db.close()
