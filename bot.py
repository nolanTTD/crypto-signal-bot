"""Spot-only research engine. No authenticated exchange or order endpoints."""
import argparse
import csv
import json
import math
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from urllib.request import Request, urlopen


def number(value, positive=True):
    n = float(value)
    if not math.isfinite(n) or (n <= 0 if positive else n < 0):
        raise ValueError('Invalid numeric value')
    return n


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS events (
      id TEXT PRIMARY KEY, wallet TEXT, asset TEXT, side TEXT,
      quantity REAL, price REAL, fee REAL, occurred REAL, observed REAL);
    CREATE TABLE IF NOT EXISTS paper (
      id TEXT PRIMARY KEY, asset TEXT, side TEXT, quantity REAL,
      price REAL, fee REAL, at REAL);
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    INSERT OR IGNORE INTO settings VALUES ('starting_cash','1000');
    ''')
    return db


def ingest(db, rows, now=None):
    """Trusted normalized trades only; transfers never become trading signals."""
    now = time.time() if now is None else now
    prepared = []
    for r in rows:
        if r['side'] not in ('buy', 'sell'):
            raise ValueError('Only decoded buys/sells accepted; exclude transfers')
        if not r['id'] or not r['wallet'] or not r['asset']:
            raise ValueError('Missing identity')
        at = number(r['occurred'])
        if at > now:
            raise ValueError('Future event')
        prepared.append((r['id'], r['wallet'], r['asset'], r['side'],
                         number(r['quantity']), number(r['price']),
                         number(r.get('fee', 0), False), at, now))
    with db:
        db.executemany('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?)', prepared)


def rankings(db):
    """Average-cost realized PnL on known inventory; incomplete histories flagged."""
    wallets = defaultdict(lambda: {'realized_pnl': 0., 'sell_fills': 0,
                                   'winning_sell_fills': 0, 'incomplete': False})
    lots = defaultdict(lambda: [0., 0.])
    for r in db.execute('SELECT * FROM events ORDER BY occurred,id'):
        w = wallets[r['wallet']]
        lot = lots[(r['wallet'], r['asset'])]
        q, p, fee = r['quantity'], r['price'], r['fee']
        if r['side'] == 'buy':
            lot[0] += q
            lot[1] += q * p + fee
        elif q <= lot[0] + 1e-12 and lot[0] > 0:
            cost = lot[1] * min(q / lot[0], 1)
            pnl = q * p - fee - cost
            lot[0] = max(0, lot[0] - q)
            lot[1] = max(0, lot[1] - cost)
            w['realized_pnl'] += pnl
            w['sell_fills'] += 1
            w['winning_sell_fills'] += int(pnl > 0)
        else:
            # Unknown inventory contaminates subsequent average-cost calculations.
            w['incomplete'] = True
            lot[:] = [0., 0.]
    return sorted([dict(wallet=k, **v) for k, v in wallets.items()],
                  key=lambda w: (w['incomplete'], -w['realized_pnl']))


def consensus(db, asset, approved, now, window=300, min_wallets=3):
    # Approved wallet set must be frozen BEFORE evaluation, not picked from future winners.
    votes = {}
    for r in db.execute('SELECT * FROM events WHERE asset=? AND occurred BETWEEN ? AND ? '
                        'AND observed<=? ORDER BY occurred,id',
                        (asset, now-window, now, now)):
        if r['wallet'] in approved:
            votes[r['wallet']] = r['side']
    return sum(v == 'buy' for v in votes.values()) >= min_wallets and not any(
        v == 'sell' for v in votes.values())


def portfolio(db):
    cash = float(db.execute("SELECT value FROM settings WHERE key='starting_cash'").fetchone()[0])
    positions = defaultdict(float)
    for r in db.execute('SELECT * FROM paper ORDER BY at,id'):
        sign = 1 if r['side'] == 'buy' else -1
        positions[r['asset']] += sign * r['quantity']
        cash -= sign * r['quantity'] * r['price'] + r['fee']
    return cash, dict(positions)


def paper_order(db, order_id, asset, side, quantity, quote, now=None):
    """Simulated bid/ask execution with explicit fee and slippage assumptions."""
    now = time.time() if now is None else now
    q = number(quantity)
    if side not in ('buy', 'sell'):
        raise ValueError('Invalid side')
    bid, ask = number(quote['bid']), number(quote['ask'])
    age = now - number(quote['at'])
    if not 0 <= age <= 30 or bid > ask or (ask-bid)/ask > .01:
        raise ValueError('Stale, future, crossed, or wide-spread quote')
    # Research defaults, NOT verified Crypto.com costs.
    p = ask * 1.001 if side == 'buy' else bid * .999
    fee = q * p * .006
    with db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM paper WHERE id=?', (order_id,)).fetchone():
            return False
        cash, positions = portfolio(db)
        if side == 'buy':
            if positions.get(asset, 0) > 1e-12:
                raise ValueError('One open position per asset')
            if q*p > 100 or sum(v > 1e-12 for v in positions.values()) >= 3:
                raise ValueError('Paper exposure cap')
            if q*p + fee > cash:
                raise ValueError('Insufficient simulated cash')
        elif q > positions.get(asset, 0) + 1e-12:
            raise ValueError('Spot only: cannot sell unowned tokens')
        db.execute('INSERT INTO paper VALUES (?,?,?,?,?,?,?)',
                   (order_id, asset, side, q, p, fee, now))
    return True


def ticker(asset):
    if asset not in ('BTC', 'ETH', 'SOL'):
        raise ValueError('Research market allowlist: BTC ETH SOL')
    req = Request('https://api.exchange.coinbase.com/products/'+asset+'-USD/ticker',
                  headers={'User-Agent': 'crypto-signal-research/0.1'})
    with urlopen(req, timeout=10) as response:
        r = json.load(response)
    return {'bid': number(r['bid']), 'ask': number(r['ask']),
            'at': datetime.fromisoformat(r['time'].replace('Z', '+00:00')).timestamp(),
            'source': 'Coinbase public ticker; not a Crypto.com execution quote'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', default='research.sqlite3')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    sub.add_parser('rank')
    i = sub.add_parser('import-trades'); i.add_argument('csv')
    t = sub.add_parser('quote'); t.add_argument('asset')
    args = p.parse_args()
    db = connect(args.db)
    if args.command == 'import-trades':
        with open(args.csv, newline='') as f:
            ingest(db, csv.DictReader(f))
        print('Imported decoded trades; no orders placed')
    elif args.command == 'rank':
        print(json.dumps(rankings(db), indent=2))
    elif args.command == 'quote':
        print(json.dumps(ticker(args.asset), indent=2))
    else:
        cash, positions = portfolio(db)
        print(json.dumps({'mode': 'paper-only', 'live_execution': False,
                          'wallet_feed': 'not connected', 'cash': cash,
                          'positions': positions}, indent=2))


if __name__ == '__main__':
    main()
