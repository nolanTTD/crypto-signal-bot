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

    def native_tx(self, buy=True, quote_usdc=False):
        token={'userAccount':W,'mint':USDC if quote_usdc else T,
               'rawTokenAmount':{'decimals':6,'tokenAmount':'100000000'}}
        event={'tokenInputs':[],'tokenOutputs':[]}
        event['nativeInput' if buy else 'nativeOutput']={'account':W,'amount':'1000000000'}
        event['tokenOutputs' if buy else 'tokenInputs']=[token]
        return {'type':'SWAP','feePayer':W,'signature':'native','timestamp':1000,
                'events':{'swap':event},
                'nativeTransfers':[{'fromUserAccount':W,'toUserAccount':T,'amount':1000000000}]}

    def test_native_swap_quote_sol_and_usdc(self):
        f, reason=decode(self.native_tx(),W)
        self.assertIsNone(reason)
        self.assertEqual((f['side'],f['quantity'],f['quote_amount'],f['quote_asset']),
                         ('buy',100,1,SOL))
        self.assertEqual(decode(self.native_tx(False),W)[0]['side'],'sell')
        usdc=decode(self.native_tx(True,True),W)[0]
        self.assertEqual((usdc['asset'],usdc['side'],usdc['quantity'],usdc['quote_amount']),
                         (SOL,'sell',1,100))

    def test_native_event_rejects_owner_decimals_and_duplicate_wrap(self):
        a=self.native_tx();a['events']['swap']['nativeInput']['account']=T
        b=self.native_tx();b['events']['swap']['tokenOutputs'][0]['rawTokenAmount']['decimals']=100
        c=self.native_tx();c['events']['swap']['tokenOutputs'][0]['mint']=SOL
        d=self.native_tx();d['events']['swap']['nativeInput']['amount']='NaN'
        for row in [a,b,c,d]:self.assertIsNone(decode(row,W)[0])

    def test_inner_swaps_never_count_twice(self):
        row=self.native_tx()
        row['events']['swap']['innerSwaps']=[row['events']['swap'].copy()]*3
        self.assertEqual(decode(row,W)[0]['quote_amount'],1)

    def test_sol_and_usdc_cost_basis_not_mixed(self):
        db=sqlite3.connect(':memory:')
        a=self.native_tx(); a['signature']='buy-sol'
        b=tx('sell-usdc','sell');b['tokenTransfers'][1]['mint']=T
        def fetch(w,k,before):return [a,b] if before is None else []
        collect(db,W,'unused',fetch=fetch)
        report=screen(db,[W])[0]
        self.assertEqual(report['decoded_swaps'],2)
        self.assertEqual(report['matched_sell_fills'],0)
        self.assertIn('unknown_cost_basis',report['qualification_blocks'])
        db.close()
