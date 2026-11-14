"""Hosted research collector. Public endpoints expose no portfolio or credentials."""
import hmac
import json
import os
import signal
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from helius_feed import collect
from discovery import discover, screen, setup

STOP = threading.Event()
STATE = {'mode': 'research-only', 'live_execution': False, 'collection': 'waiting',
         'last_success': None, 'last_error': None, 'new_transactions': 0}
LOCK = threading.Lock()


def prerequisites():
    missing = []
    if not os.environ.get('HELIUS_API_KEY', '').strip():
        missing.append('HELIUS_API_KEY')
    wallets = list(dict.fromkeys(w.strip() for w in os.environ.get('WATCH_WALLETS', '').split(',') if w.strip()))
    if not wallets and os.environ.get('DISCOVERY_ENABLED') != 'true':
        missing.append('WATCH_WALLETS_OR_DISCOVERY_ENABLED')
    if len(wallets) > 5:
        missing.append('WATCH_WALLETS_MAX_5')
    # Collector deliberately refuses ephemeral storage on the hosted service.
    if not os.path.ismount('/var/data'):
        missing.append('PERSISTENT_DISK_AT_/var/data')
    return missing, wallets


def snapshot():
    with LOCK:
        result = dict(STATE)
    result['missing_configuration'], _ = prerequisites()
    result['paper_strategy_loop'] = 'not implemented'
    return result


def loop():
    while not STOP.is_set():
        missing, wallets = prerequisites()
        with LOCK:
            STATE['collection'] = 'waiting_for_configuration' if missing else 'collecting'
        if not missing:
            try:
                count = 0
                gaps = False
                with sqlite3.connect('/var/data/research.sqlite3', timeout=30) as db:
                    setup(db)
                    if not wallets:
                        wallets = discover(db, os.environ['HELIUS_API_KEY'])
                    for wallet in wallets:
                        if STOP.is_set():
                            break
                        r = collect(db, wallet, os.environ['HELIUS_API_KEY'], max_pages=1)
                        count += r['new_transactions']
                        gaps = gaps or not r['caught_up']
                    reports = screen(db, wallets)
                    meta = dict(db.execute('SELECT key,value FROM discovery_meta')) if os.environ.get('DISCOVERY_ENABLED') == 'true' else {}
                with LOCK:
                    STATE.update(collection=(('discovery_error' if int(meta.get('seed_request_errors', 0)) else 'no_candidates') if not wallets else ('history_gap' if gaps else 'caught_up')),
                                 last_success=time.time() if wallets else STATE.get('last_success'),
                                 last_error='Discovery requests failed' if int(meta.get('seed_request_errors',0)) else None)
                    STATE['new_transactions'] += count
                    STATE['wallet_reports'] = reports
                    STATE['discovery'] = meta
                print(json.dumps({'event':'research_cycle', 'wallet_count':len(wallets),
                                  'new_transactions':count, 'history_gap':gaps,
                                  'qualified_wallets':0}), flush=True)
            except Exception:
                # Third-party and database exceptions may contain URLs or data.
                with LOCK:
                    STATE.update(collection='error', last_error='Collection failed; sensitive details suppressed')
                print('Research cycle failed; sensitive details suppressed', flush=True)
        STOP.wait(3600)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/health'):
            # Process health only; NOT a claim of feed or strategy readiness.
            code, data = 200, {'service': 'crypto-signal-research', 'process': 'running',
                               'live_trading': False, 'feed_status': 'use authenticated /status'}
        elif self.path == '/status':
            token = os.environ.get('STATUS_TOKEN', '')
            supplied = self.headers.get('Authorization', '')
            if not token or not hmac.compare_digest(supplied, 'Bearer '+token):
                code, data = 401, {'error': 'Authentication required'}
            else:
                code, data = 200, snapshot()
        else:
            code, data = 404, {'error': 'Not found'}
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # Never log request paths, headers, or query-string credentials.


def main():
    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '10000'))), Handler)
    worker = threading.Thread(target=loop, daemon=True)
    worker.start()
    def terminate(*_):
        STOP.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    print('Research service started; live trading is not implemented', flush=True)
    try:
        server.serve_forever()
    finally:
        STOP.set()
        worker.join(timeout=20)
        server.server_close()


if __name__ == '__main__':
    main()
