"""Bounded read-only candidate discovery. No execution or automatic qualification."""
import json
import math
import re
import time
from collections import Counter, defaultdict
from helius_feed import history

# Canonical Solana mints: wrapped SOL and Circle native USDC.
SOL = 'So11111111111111111111111111111111111111112'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
SEEDS = (SOL, USDC)
ADDRESS = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}')


def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS discovery_evidence (
      seed TEXT, signature TEXT, wallet TEXT, observed REAL, payload TEXT,
      PRIMARY KEY(seed,signature));
    CREATE TABLE IF NOT EXISTS research_roster (
      wallet TEXT PRIMARY KEY, selected REAL, source TEXT);
    CREATE TABLE IF NOT EXISTS discovery_meta (key TEXT PRIMARY KEY, value TEXT);
    ''')


def discover(db, key, fetch=history, now=None):
    """At most two 100-transaction seed requests, once per day.

    Seed mint histories are not exhaustive DEX histories. Fee payers are only
    candidates and may be relayers; no person/entity identity is inferred.
    """
    setup(db)
    now = time.time() if now is None else now
    last = db.execute("SELECT value FROM discovery_meta WHERE key='last_attempt'").fetchone()
    if last and now-float(last[0]) < 86400:
        return [r[0] for r in db.execute('SELECT wallet FROM research_roster ORDER BY wallet')]
    # Persist budget before requests, so a failing provider is not hammered.
    with db:
        db.execute("INSERT OR REPLACE INTO discovery_meta VALUES ('last_attempt',?)", (str(now),))
    errors = 0
    for seed in SEEDS:
        try:
            rows = fetch(seed, key)
            with db:
                for tx in rows:
                    wallet = tx.get('feePayer', '')
                    if (tx.get('type') != 'SWAP' or tx.get('transactionError')
                            or not ADDRESS.fullmatch(wallet) or wallet in SEEDS):
                        continue
                    db.execute('INSERT OR IGNORE INTO discovery_evidence VALUES (?,?,?,?,?)',
                               (seed, tx['signature'], wallet, now, json.dumps(tx)))
        except (ValueError, RuntimeError):
            errors += 1
    counts = Counter(r[0] for r in db.execute(
        'SELECT wallet FROM discovery_evidence WHERE observed>=? GROUP BY wallet,signature',
        (now-86400,)))
    # Freeze first research roster. Do not churn it toward apparent winners.
    if not db.execute('SELECT 1 FROM research_roster LIMIT 1').fetchone():
        with db:
            for wallet, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:5]:
                db.execute('INSERT INTO research_roster VALUES (?,?,?)',
                           (wallet, now, 'swap fee-payer candidate; ownership unverified'))
    with db:
        for k, v in {'last_completed': now, 'seed_request_errors': errors,
                     'candidate_addresses': len(counts)}.items():
            db.execute('INSERT OR REPLACE INTO discovery_meta VALUES (?,?)', (k, str(v)))
    return [r[0] for r in db.execute('SELECT wallet FROM research_roster ORDER BY wallet')]


def decode(tx, wallet):
    """Conservative token-only USDC swaps; returns quote-denominated evidence.

    Reject extra token legs, unknown ownership, native SOL legs and side transfers.
    USDC amounts are NOT assumed to be exactly equal to USD.
    """
    if tx.get('type') != 'SWAP' or tx.get('transactionError'):
        return None, 'not_successful_swap'
    if tx.get('feePayer') != wallet:
        return None, 'payer_ownership_unverified'
    # Native SOL rent, wrapping and fees need a separate decoder; fail closed.
    if any(t.get('fromUserAccount') == wallet or t.get('toUserAccount') == wallet
           for t in tx.get('nativeTransfers', [])):
        return None, 'native_sol_leg_not_supported'
    legs = []
    try:
        for t in tx.get('tokenTransfers', []):
            src, dst = t.get('fromUserAccount'), t.get('toUserAccount')
            if src == wallet and dst == wallet:
                continue
            if wallet not in (src, dst):
                continue
            n = float(t['tokenAmount'])
            if not math.isfinite(n) or n <= 0 or not ADDRESS.fullmatch(t['mint']):
                return None, 'invalid_amount_or_mint'
            legs.append((t['mint'], -n if src == wallet else n))
        if len(legs) != 2 or len({x[0] for x in legs}) != 2:
            return None, 'ambiguous_or_unsupported_legs'
        stable = [x for x in legs if x[0] == USDC]
        if len(stable) != 1:
            return None, 'no_native_usdc_quote'
        other = next(x for x in legs if x[0] != USDC)
        if stable[0][1]*other[1] >= 0:
            return None, 'not_opposing_legs'
        at = float(tx['timestamp'])
        if not math.isfinite(at) or at <= 0:
            return None, 'invalid_timestamp'
        return {'id': tx['signature'], 'asset': other[0], 'quantity': abs(other[1]),
                'quote_amount': abs(stable[0][1]), 'at': at,
                'side': 'buy' if other[1] > 0 else 'sell'}, None
    except (ValueError, TypeError, KeyError, StopIteration):
        return None, 'malformed_swap'


def screen(db, wallets):
    """Describe observed samples; never approve a wallet for live copying."""
    reports = []
    for wallet in wallets:
        fills, skips = [], Counter()
        for r in db.execute('SELECT payload FROM raw_helius WHERE wallet=?', (wallet,)):
            tx = json.loads(r[0])
            fill, reason = decode(tx, wallet)
            if fill:
                fills.append(fill)
            else:
                skips[reason] += 1
        lots = defaultdict(lambda: [0., 0.])
        pnl = 0.
        sells = 0
        unknown = False
        for f in sorted(fills, key=lambda x: (x['at'], x['id'])):
            lot = lots[f['asset']]
            if f['side'] == 'buy':
                lot[0] += f['quantity']; lot[1] += f['quote_amount']
            elif lot[0] > 0 and f['quantity'] <= lot[0]+1e-12:
                cost = lot[1]*min(1, f['quantity']/lot[0])
                pnl += f['quote_amount']-cost
                sells += 1
                lot[0] = max(0, lot[0]-f['quantity'])
                lot[1] = max(0, lot[1]-cost)
            else:
                unknown = True
                lot[:] = [0., 0.]
        reasons = ['bounded_history', 'ownership_not_verified', 'forward_test_required',
                   'fees_and_copy_costs_not_deducted', 'crypto_com_mapping_not_verified']
        if len(fills) < 20 or sells < 10:
            reasons.append('insufficient_decoded_trades')
        if unknown:
            reasons.append('unknown_cost_basis')
        if skips:
            reasons.append('unsupported_history_items')
        reports.append({'wallet': wallet, 'status': 'research_candidate_only',
                        'decoded_swaps': len(fills), 'matched_sell_fills': sells,
                        'sample_realized_gross_usdc': round(pnl, 6),
                        'skipped_items': dict(skips), 'qualification_blocks': reasons})
    return reports
