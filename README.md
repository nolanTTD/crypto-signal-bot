# Crypto Signal Bot — research foundation

**Status: paper-only core, not deployed; no continuous wallet feed or live orders.**
No API keys, exchange account, wallet private key, or personal holdings are required.
Python 3.11+; standard library only.

## Run

```sh
python -m unittest discover -s tests -v
python bot.py status
python bot.py quote SOL
python bot.py import-trades decoded_trades.csv
python bot.py rank
```

Public quotes use Coinbase market data, not Crypto.com prices. Network access is required
for `quote`; account access is not. An old last-trade timestamp is conservatively rejected
by the simulator even if its bid/ask is current.

## What is implemented

- Atomic, idempotent SQLite ingestion of normalized buy/sell fills.
- Average-cost realized USD PnL and missing-cost-basis flags. Sell-fill counts are
  not round-trip counts. Incomplete wallets must be excluded from selection.
- Consensus function over a preselected wallet roster: each address gets one vote;
  any sell vote vetoes entry. Roster selection must precede evaluation.
- Paper execution with bid/ask, illustrative 0.6% fee and 0.1% slippage per side,
  freshness/spread checks, $100 position cap, three positions maximum, no shorts
  and no borrowing. These are simulation defaults, not approved live limits.
- Durable paper ledger, transaction locking, duplicate order IDs and insufficient
  cash checks. No authenticated exchange endpoints exist in this codebase.

## Input contract

CSV headers: `id,wallet,asset,side,quantity,price,fee,occurred`.
`occurred` is a UTC Unix timestamp; prices and fees are USD. `id` must uniquely
identify each fill (chain + transaction + instruction/log index). `asset` should
be chain + contract/mint for wallet analysis, not an ambiguous ticker.
Imports are timestamped when observed. Transfers, airdrops and unsupported
transactions must not be represented as buys/sells. Historical imports cannot
be replayed as if known earlier. Wallet rankings are descriptive, not evidence
of future performance. No annualized return or return-on-capital is inferred.

## Architecture decision

Reviewed references (no third-party code copied):
- https://github.com/crypto-com/crypto-agent-trading : official App quote/confirm
  execution flow. Integrate later behind a separate risk-controlled adapter.
- https://github.com/Kelows/million : wallet-to-token discovery, independent-owner
  consensus, token checks and shadow portfolios. It is Solana/DEX oriented;
  directly adopting its executor would not give Crypto.com App execution.

We use explicit boundaries: decoded wallet feed -> identity/quality checks ->
wallet scoring -> signal -> risk checks -> paper executor -> durable audit log.
Live execution will be a separate reviewed component, not an environment switch
that silently makes this research engine spend money.

## Remaining before an actual 24/7 test

1. Connect a decoded blockchain data provider (initial candidate: Helius for Solana),
   review current costs/limits, and add an ingestion adapter with retries and checkpoints.
2. Discover and evaluate wallets; identify common owners, routers and bots. Current
   consensus counts addresses, NOT independently verified owners.
3. Add contract-to-Crypto.com asset mapping and verified available-token/liquidity filters.
4. Wire signals to fresh execution quotes, simulated exits, mark-to-market equity,
   drawdowns, benchmarks, adverse cost scenarios and rejection logs. The core functions
   here do not yet run an autonomous strategy loop.
5. Freeze selection rules before forward testing; evaluate on new data after costs.
6. Confirm Render workspace, add monitoring and persistent storage, verify restart
   recovery, and deploy. No always-on service has been provisioned.
7. Only after review: securely configure a new Crypto.com key and separately approve
   a small live mandate. Never put credentials or private portfolio data in this repo.

## Security

The repository is public. Keep all keys in hosting secret settings and all runtime
data outside Git. Logs must not include secrets. The previously exposed key is not
included. No withdrawal, transfer, bank or funding functionality is implemented.

## Helius collector (added)

`helius_feed.py` reads finalized enhanced transaction history and saves raw
responses in SQLite. It is a read-only, bounded polling building block, NOT a
live trading loop or wallet ranking feed. It does not yet convert swaps into
USD-priced fills. The enhanced endpoint is documented by Helius as legacy;
keep this adapter isolated for future migration to Parsed Events.

Set `HELIUS_API_KEY` directly in secure environment settings, never in chat or
Git. Then run:

```sh
python helius_feed.py PUBLIC_SOLANA_WALLET --db research.sqlite3 --max-pages 5
```

Each page requests at most 100 transactions. Initial history is bounded and
explicitly incomplete. On later runs the collector scans back to the prior
head. If the page budget is exhausted, it reports `caught_up: false` and does
not advance that cursor. Increase the bounded page budget or backfill before
using the wallet as a signal source. Do not infer that an address is a whale
merely because it has been collected. API failures are redacted to avoid
printing URL-embedded credentials. Tests use fixtures; a real Helius
connection has not been tested without a securely configured key.

Source: https://www.helius.dev/docs/api-reference/enhanced-transactions/gettransactionsbyaddress

## Hosted collector service

Start command: `python service.py`. Build command:
`python -m unittest discover -s tests -v` (no third-party packages required).

The public `/health` endpoint reports process availability only; it is not a feed
readiness check. `/status` requires `Authorization: Bearer <STATUS_TOKEN>` and shows
missing configuration and collector freshness without wallet addresses or secrets.
Set a long random `STATUS_TOKEN` directly in Render Environment settings.

The collector waits until ALL prerequisites are present:
- `HELIUS_API_KEY`: set directly in Render Environment.
- `WATCH_WALLETS`: comma-separated public Solana addresses, at most five. These
  are research candidates, not automatically approved profitable wallets.
- A persistent disk mounted at `/var/data`. The service verifies the mount
  and refuses to collect onto ephemeral storage.

With configuration complete it collects at most two pages per wallet every five
minutes. This cadence is for initial data validation, not instant copy trading.
Failures and history gaps are shown in authenticated status. A capped page scan
can remain behind a very active wallet; do not use it for signals until caught up.
Raw histories are not automatically USD-priced, ranked, or paper-traded yet.
Only one service instance should run against this SQLite disk.

For always-on collection use paid compute plus a persistent disk. Free compute
is suitable for the initial setup service only, may sleep, and cannot provide
24/7 monitoring. No authenticated exchange or live execution is implemented.
