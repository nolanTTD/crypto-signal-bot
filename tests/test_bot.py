import unittest
from bot import connect, ingest, rankings, paper_order, portfolio, consensus


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
        self.quote = {'bid': 99.9, 'ask': 100, 'at': 1000}

    def tearDown(self):
        self.db.close()

    def event(self, id, side='buy', wallet='a', q=1, price=100):
        return dict(id=id, wallet=wallet, asset='SOL', side=side,
                    quantity=q, price=price, fee=1, occurred=990)

    def test_dedup_and_pnl(self):
        buy = self.event('1')
        ingest(self.db, [buy, buy, self.event('2', 'sell', price=110)], 1000)
        self.assertEqual(rankings(self.db)[0]['realized_pnl'], 8)

    def test_missing_cost_basis_flag(self):
        ingest(self.db, [self.event('1', 'sell')], 1000)
        self.assertTrue(rankings(self.db)[0]['incomplete'])
        self.assertEqual(rankings(self.db)[0]['realized_pnl'], 0)

    def test_transfer_and_nan_rejected_atomically(self):
        for row in [self.event('1', 'transfer'), self.event('2', q='NaN')]:
            with self.assertRaises(ValueError):
                ingest(self.db, [self.event('ok'), row], 1000)
        self.assertEqual(len(rankings(self.db)), 0)

    def test_no_short_or_leverage(self):
        with self.assertRaises(ValueError):
            paper_order(self.db, 's', 'SOL', 'sell', 1, self.quote, 1000)
        with self.assertRaises(ValueError):
            paper_order(self.db, 'b', 'SOL', 'buy', 2, self.quote, 1000)

    def test_fill_costs_idempotency_restart(self):
        self.assertTrue(paper_order(self.db, 'b', 'SOL', 'buy', .9, self.quote, 1000))
        self.assertFalse(paper_order(self.db, 'b', 'SOL', 'buy', .9, self.quote, 1000))
        self.assertLess(portfolio(self.db)[0], 910)
        paper_order(self.db, 's', 'SOL', 'sell', .9, self.quote, 1000)
        self.assertLess(portfolio(self.db)[0], 1000)
        self.assertAlmostEqual(portfolio(self.db)[1]['SOL'], 0)

    def test_stale_wide_and_future_quotes(self):
        for quote in [dict(self.quote, at=960), dict(self.quote, at=1001),
                      dict(self.quote, bid=80)]:
            with self.assertRaises(ValueError):
                paper_order(self.db, 'b', 'SOL', 'buy', .9, quote, 1000)

    def test_consensus_no_lookahead_or_duplicate_votes(self):
        ingest(self.db, [self.event(str(i), wallet=w) for i,w in enumerate('aaabc')], 1000)
        self.assertFalse(consensus(self.db, 'SOL', {'a','b','c'}, 999))
        self.assertTrue(consensus(self.db, 'SOL', {'a','b','c'}, 1000))
        self.assertFalse(consensus(self.db, 'SOL', {'a','b'}, 1000))


if __name__ == '__main__':
    unittest.main()
