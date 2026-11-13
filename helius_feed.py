"""Read-only Helius history collector. Stores raw evidence, never creates orders."""
import argparse
import json
import os
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'https://api-mainnet.helius-rpc.com/v0/addresses/'


def history(wallet, key, before=None, opener=urlopen):
    if not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,44}', wallet):
        raise ValueError('Invalid Solana address')
    if not key or not key.strip():
        raise ValueError('Configure HELIUS_API_KEY in secure environment settings')
    query = {'api-key': key, 'limit': 100, 'commitment': 'finalized',
             'token-accounts': 'balanceChanged'}
    if before:
        query['before-signature'] = before
    request = Request(BASE + wallet + '/transactions?' + urlencode(query),
                      headers={'User-Agent': 'crypto-signal-research/0.2'})
    try:
        with opener(request, timeout=15) as response:
            rows = json.load(response)
    except HTTPError as e:
        # Do not propagate the request URL: it contains the API key.
        raise RuntimeError('Helius HTTP '+str(e.code)+'; request URL redacted') from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError('Helius network or response error; request details redacted') from None
    if not isinstance(rows, list) or any(not isinstance(r, dict) or
                                        not r.get('signature') for r in rows):
        raise RuntimeError('Unexpected Helius response schema')
    return rows


def collect(db, wallet, key, max_pages=5, fetch=history):
    """Overlapping newest-first scans. A page cap never advances past unseen gaps.

    Initial snapshots are intentionally bounded and labelled incomplete. Repeated
    runs catch up newest activity by scanning back to the last observed signature.
    """
    if not 1 <= max_pages <= 20:
        raise ValueError('Page cap must be between 1 and 20')
    db.executescript('''CREATE TABLE IF NOT EXISTS raw_helius (
      wallet TEXT, signature TEXT, observed REAL, payload TEXT,
      PRIMARY KEY(wallet,signature));
      CREATE TABLE IF NOT EXISTS helius_cursor (wallet TEXT PRIMARY KEY, head TEXT);
    ''')
    cursor = db.execute('SELECT head FROM helius_cursor WHERE wallet=?', (wallet,)).fetchone()
    previous = cursor[0] if cursor else None
    before = None
    newest = None
    caught_up = False
    count = 0
    for _ in range(max_pages):
        rows = fetch(wallet, key, before)
        if not rows:
            caught_up = True
            break
        if newest is None:
            newest = rows[0]['signature']
        with db:
            for row in rows:
                sig = row['signature']
                if sig == previous:
                    caught_up = True
                    break
                result = db.execute('INSERT OR IGNORE INTO raw_helius VALUES (?,?,?,?)',
                                    (wallet, sig, time.time(), json.dumps(row)))
                count += result.rowcount
        if caught_up:
            break
        next_before = rows[-1]['signature']
        if next_before == before:
            break
        before = next_before
    # First run establishes a bounded baseline, explicitly not complete history.
    if newest and (previous is None or caught_up):
        with db:
            db.execute('INSERT OR REPLACE INTO helius_cursor VALUES (?,?)', (wallet, newest))
    return {'new_transactions': count, 'caught_up': caught_up,
            'initial_snapshot': previous is None, 'full_history_verified': False,
            'orders_placed': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('wallet')
    p.add_argument('--db', default='research.sqlite3')
    p.add_argument('--max-pages', type=int, default=5)
    args = p.parse_args()
    try:
        with sqlite3.connect(args.db) as db:
            result = collect(db, args.wallet, os.environ.get('HELIUS_API_KEY'), args.max_pages)
        print(json.dumps(result, indent=2))
    except (ValueError, RuntimeError) as e:
        p.exit(1, str(e)+'\n')


if __name__ == '__main__':
    main()
