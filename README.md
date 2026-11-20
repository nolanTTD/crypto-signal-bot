# Crypto Signal Research

**Read-only discovery and screening service; live trading is not implemented.**
The Render service is deployed with persistent storage. Its current readiness and
actual counts must be checked using authenticated `/status`; process health alone
is not evidence that collection or strategy research is succeeding.

Python 3.11+; standard library only. No private wallet, exchange credentials,
account holdings, deposits or withdrawals are needed for this research service.

## Run and test

```sh
python -m unittest discover -s tests -v
python bot.py status
python bot.py quote SOL
python bot.py import-trades decoded_trades.csv
python bot.py rank
python service.py
```

The manual paper ledger and public quote command are separate from the hosted
collector. Coinbase bid/ask is public reference data, not Crypto.com executable
pricing. No autonomous paper strategy loop has been connected yet.

## Hosted configuration

Start: `python service.py`. Build: `python -m unittest discover -s tests -v`.
One paid Render instance, with a 1 GB disk mounted at `/var/data`.

Set directly in Render Environment settings:
- `HELIUS_API_KEY`: private Helius credential. Never paste it into chat or Git.
- `STATUS_TOKEN`: long random private token for `/status`.
- `DISCOVERY_ENABLED=true`: enable bounded discovery if no explicit roster is set.
- Optional `WATCH_WALLETS`: comma-separated public addresses, maximum five;
  overrides automatic discovery and must contain only research candidates.

Without a mounted disk or key the collector waits. Public `/health` is a
process-only check; `/status` requires `Authorization: Bearer <STATUS_TOKEN>`.
There is no public portfolio, wallet list, control endpoint or trading API.

## Discovery and screening

`discovery.py` samples recent finalized history for the canonical wrapped SOL
and Circle USDC mints. These are narrow, incomplete discovery samples, NOT an
exhaustive DEX or full-market scan. Successful SWAP fee payers become candidates;
fee payers may be routers, relayers or bots and are not automatically identified
as traders. Source transactions and selection time are retained in SQLite.

The first five research candidates are frozen into a roster, selected by observed
swap count, NOT by future profits. Two seed-history requests run at most daily;
each roster wallet gets at most one 100-transaction page hourly. Normal operation
uses at most 122 history requests/day (2 + 5*24), excluding manual restarts and
invocations. Check actual credit consumption in Helius. No upgrades are automatic.
This initial cadence validates data and is not suitable for instant copy trading.

`helius_feed.py` deduplicates raw transactions and checkpoints history. An initial
bounded snapshot is never labelled full history. Subsequent capped scans report
gaps and preserve the cursor if they fail to reach it. Very active wallets can
outgrow the scan budget and must not be used for signals in that condition.

Reports under authenticated `/status` include:
- Candidate addresses and counts of conservatively decoded swaps.
- Matched sell fills and sample gross realized PnL in **USDC units**, not USD.
- Reasons for skipping history items and blocking qualification.

Only simple two-token native-USDC swaps with wallet-owned transfer legs are
decoded. Native SOL, ambiguous extra legs, failed transactions and transfers are
rejected. Gross sample PnL excludes network fees, copy delay, spread and slippage;
USDC is not assumed exactly equivalent to USD. Unknown cost basis is flagged.
Every candidate remains unqualified because history, ownership, independent-owner
clustering, token availability, costs and forward performance are not verified.
Reports cannot automatically authorize or enable trading.

## Manual paper engine

`bot.py` accepts normalized CSV fields:
`id,wallet,asset,side,quantity,price,fee,occurred`.
Amounts are USD for this separate manual engine. IDs must uniquely identify fills;
assets should be chain + mint/contract. Occurred timestamps are UTC Unix seconds;
observed timestamps are recorded at ingestion. Transfers are not fills.

The paper executor uses bid/ask with illustrative 0.6% fees and 0.1% slippage each
side (NOT verified Crypto.com costs), quote freshness/spread checks, a $100
position cap, maximum three positions, no shorts, no leverage, transactional
locking and duplicate order protection. These are research defaults, not approved
live limits. Consensus uses a preselected address roster, not verified owners.

## Remaining work

- Validate live provider responses, API credits, retained evidence and restart recovery.
- Broaden discovery to verified liquidity pools and screen infrastructure/bot activity.
- Complete inventory histories, robust trade decoding and independent-owner detection.
- Map contracts to currently available Crypto.com tokens and liquidity constraints.
- Connect forward paper signals, entries/exits, mark-to-market, drawdowns and benchmarks.
- Test new-data performance after realistic execution costs and adverse scenarios.
- Separately review and approve a live mandate only if supported by results.

## Security and provenance

The repo is public. Credentials and runtime databases must never be committed.
All public HTTP endpoints omit sensitive details. API URLs containing keys and
third-party error strings are suppressed from logs. Trading, funding, transfers,
withdrawals and bank operations are not implemented.

No third-party code copied. Architecture references reviewed:
- https://github.com/crypto-com/crypto-agent-trading
- https://github.com/Kelows/million

API/schema references:
- https://www.helius.dev/docs/api-reference/enhanced-transactions/gettransactionsbyaddress
- https://help.circle.com/support/en/usdc-supported-blockchains-minting-redemption-faqs?id=kb_article_view&sysparm_article=KB0010590
- https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker

Helius labels Enhanced Transactions as legacy; keep that adapter isolated for
migration. No wallet-performance or profitability claims have been verified.


## Decoder and resumable history update

Native SOL swaps now use explicit outer `events.swap` inputs/outputs with verified
wallet ownership fields and exact lamport/decimal conversion. Nested route swaps
are not counted again; ambiguous, missing-owner and malformed events are rejected.
SOL-quoted and USDC-quoted cost basis/PnL remain separate. These are gross samples,
not net performance or USD returns. Token-only fallback remains conservative.

Gap scans now persist target, newest boundary and before-signature in a separate
SQLite table. Later hourly cycles resume the unfinished page range instead of
repeatedly fetching the newest page. Advancing the completed head is atomic with
page storage. No extra requests or paid-plan changes are introduced. Catch-up
can still take time and busy wallets can still outrun the budget; pending scans
and incomplete coverage remain blockers to using a wallet as a signal.
