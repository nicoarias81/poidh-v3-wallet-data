# poidh v3 — wallet activity dataset (snapshot 2026-11-08T03:59:54Z)

On-chain dataset of historical poidh v3 activity for every wallet that interacted
with the poidh v3 contracts on **Ethereum mainnet, Arbitrum and Base**, produced
for [issue #1540](https://github.com/picsoritdidnthappen/poidh-app/issues/1540).

**Main deliverable:** [`output/poidh_v3_wallet_data.csv`](output/poidh_v3_wallet_data.csv) —
**1,413 unique wallets**, 13 columns (see schema below). The row set is every address
that appears in any poidh v3 event on the three chains (bounty issuer, open-bounty
contributor, claim issuer, refund/withdraw participant) plus every current NFT holder,
so wallets with zero in all value columns are included as **0** rather than omitted.
450 of those 1,413 have a non-zero value in at least one column.
**Claim image:** [`chart/poidh_v3_wallets.png`](chart/poidh_v3_wallets.png).

## Contracts

| Chain | Chain ID | Core (PoidhV3) | Claim NFT (ERC-721) |
|---|---|---|---|
| Ethereum mainnet | 1 | `0xe731dfadbff20542e10d09d26fc71445c70d4232` | `0x9c5f45d5e1382e4058d334d93c6c01442012a4d9` |
| Arbitrum One | 42161 | `0x5555fa783936c260f77385b4e153b9725fef1719` | `0x27e117cc9a8da363442e7bd0618939e3eeeacf6a` |
| Base | 8453 | `0x5555fa783936c260f77385b4e153b9725fef1719` | `0x27e117cc9a8da363442e7bd0618939e3eeeacf6a` |

## Snapshot blocks

The cutoff is `2026-11-08T03:59:54Z` (UTC). For each chain we use the latest block
whose timestamp is **≤** the cutoff, verified against `eth_getBlockByNumber`:

| Chain | Snapshot block | Block timestamp (UTC) |
|---|---|---|
| Ethereum mainnet | 26,367,617 | 2026-11-08T03:59:47Z (7 s before cutoff; no block boundary inside those 7 s) |
| Arbitrum One | 522,271,780 | 2026-11-08T03:59:54Z (exact) |
| Base | 54,600,096 | 2026-11-08T03:59:54Z (exact) |

All logs ≤ the snapshot block are included; ownership is computed as of the
snapshot block.

## CSV schema (13 columns, one row per unique wallet)

`wallet_address, eth_paid_mainnet, eth_paid_arbitrum, eth_paid_base, eth_paid_total,
eth_earned_mainnet, eth_earned_arbitrum, eth_earned_base, eth_earned_total,
nfts_held_mainnet, nfts_held_arbitrum, nfts_held_base, nfts_held_total`

## Methodology

1. **Event extraction.** Every log emitted by the core contract up to the
   snapshot block is decoded with the event signatures from
   [`refs/PoidhV3.sol`](refs/PoidhV3.sol): `BountyCreated`, `BountyJoined`,
   `ClaimCreated`, `ClaimAccepted`, `BountyCancelled`, `WithdrawFromOpenBounty`,
   `RefundClaimed`. Every `Transfer` log of the claim NFT contract is decoded for
   current ownership.
2. **ETH paid.** Only bounties that were **accepted** count.
   * *Solo bounties*: the creator/funder is credited with the bounty amount they
     paid at creation (`BountyCreated.amount`).
   * *Open (crowdfunded) bounties*: each contributor is credited with their real
     contribution (`BountyJoined` amount per contributor; the creation amount
     counts for the issuer).
   * `WithdrawFromOpenBounty` amounts are subtracted from the withdrawing
     contributor. `RefundClaimed` amounts are subtracted (these only occur on
     cancelled bounties, which are excluded entirely — the subtraction is a
     safety net).
   * Bounties that were cancelled or never accepted are **excluded** from paid
     (and therefore from earned).
3. **ETH earned.** When a claim is accepted (`ClaimAccepted`), the claim issuer
   is credited with the **gross** bounty amount **before** the 2.5% protocol fee
   (`bountyAmount`), not the net payout.
4. **NFTs held.** Current ownership at the snapshot block, replaying all
   `Transfer` logs of the claim NFT contract. Tokens currently held by the core
   protocol contract (still in escrow, i.e. bounty not yet accepted) are counted
   separately (`nft_held_by_protocol_escrow`) and are **not** attributed to any
   user wallet.

### Reconciliation

For each chain, the sum of ETH credited as paid by all contributors must equal
the sum of gross ETH credited to winners:

| Chain | Created | Accepted | Cancelled | Σ paid (contributors) | Σ earned (winners, gross) | Match | Escrow NFTs |
|---|---|---|---|---|---|---|---|
| mainnet | 46 | 27 | 12 | 3.381683 ETH | 3.381683 ETH | ✅ | 380 |
| arbitrum | 161 | 113 | 36 | 2.969041 ETH | 2.969041 ETH | ✅ | 619 |
| base | 542 | 304 | 135 | 5.568481 ETH | 5.568481 ETH | ✅ | 3,714 |
| **Total** | **749** | **444** | **183** | **11.919205 ETH** | **11.919205 ETH** | ✅ | 4,713 |

Full details in [`data/reconciliation.json`](data/reconciliation.json).

Additional cross-checks:

* `bountyCounter()` read on-chain (mainnet and Arbitrum) exactly equals the number
  of decoded `BountyCreated` events (46 and 161 respectively).
* On every chain, NFT escrow + user-held NFTs equals total distinct claim NFTs
  observed (e.g. Base: 3,714 + 304 = 4,018).
* Protocol fee implied by payouts is exactly 2.5% of gross on every chain.

## Reproducing

```bash
pip install requests eth-hash matplotlib numpy
python scripts/fetch.py      # raw logs -> data/raw_logs_<chain>.json + snapshot.json
python scripts/build_csv.py  # -> output/poidh_v3_wallet_data.csv, reconciliation, chart_data
python scripts/make_chart.py # -> chart/poidh_v3_wallets.png
```

`scripts/probe.py` measures the max `eth_getLogs` range each public RPC accepts.

### Data sources per chain

* **mainnet / arbitrum**: `eth_getLogs` against public RPCs
  (`ethereum-rpc.publicnode.com`, `rpc.mevblocker.io`, `arb1.arbitrum.io/rpc`,
  `arbitrum-one-rpc.publicnode.com`), chunked adaptively.
* **base**: free RPCs could not serve archive-range `eth_getLogs`, so Base logs
  were paged from the **Base Blockscout API** (`base.blockscout.com`,
  `/api/v2/addresses/<core|nft>/logs`) up to the snapshot block, then normalized
  to the same raw-log shape (`data/raw_logs_base.json`). Ownership/order is
  derived from (blockNumber, logIndex) sorting, not page order.

## Limitations / edge cases

* **ETH paid** is scoped to *accepted* bounties by design (per issue #1540):
  contributions to open/cancelled bounties that never paid out are excluded.
* Refunds/withdrawals before acceptance are netted out of the contributor's paid
  amount.
* NFT ownership is point-in-time (snapshot block); historical holders are not
  listed. NFTs on secondary markets are attributed to the current holder.
* Blockscout topic serialization can emit `null` for non-Transfer event topics;
  only ERC-721 `Transfer` logs are used for ownership.
* Multi-claim bounties: only the accepted claim earns; other claimants' NFTs stay
  in escrow and are reported separately.
* Amounts are exact wei integers converted to a decimal ETH string (no float
  rounding beyond JSON pretty-printing).

## Repository layout

```
scripts/fetch.py      # chain scanner (eth_getLogs / Blockscout fallback)
scripts/rpc.py        # RPC client w/ sticky best provider + backoff
scripts/build_csv.py  # decode events, reconcile, write CSV
scripts/make_chart.py # claim PNG
scripts/probe.py      # RPC getLogs range probe
data/                 # raw logs + snapshot + reconciliation
output/               # the CSV
chart/                # claim image
refs/                 # contract sources used for ABI/event signatures
```
