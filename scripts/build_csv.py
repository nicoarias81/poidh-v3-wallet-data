"""Build the per-wallet CSV from raw logs + reconciliation tables + chart data.

Rules (per bounty spec #1540):
- Only completed/accepted bounties count.
- eth_paid: contributions to accepted bounties (BountyCreated amount for the
  issuer, BountyJoined amounts for contributors), minus WithdrawFromOpenBounty
  and RefundClaimed returns.
- eth_earned: the gross bounty amount BEFORE the 2.5% fee, credited to the
  claim winner (ClaimAccepted.bountyAmount -> claimIssuer).
- nfts_held: current ownership at the snapshot block from NFT Transfer logs;
  NFTs held by the protocol core (escrow) are reported separately, not as a
  user wallet.
- Bounties cancelled or never accepted are excluded entirely.

Usage: python scripts/build_csv.py
"""
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "output")
os.makedirs(OUT, exist_ok=True)

CHAINS = ["mainnet", "arbitrum", "base"]
CORE = {
    "mainnet": "0xe731dfadbff20542e10d09d26fc71445c70d4232",
    "arbitrum": "0x5555fa783936c260f77385b4e153b9725fef1719",
    "base": "0x5555fa783936c260f77385b4e153b9725fef1719",
}

TOPIC = {
    "BountyCreated": "0x" + __import__("eth_hash.auto", fromlist=["keccak"]).keccak(
        "BountyCreated(uint256,address,string,string,uint256,uint256,bool,uint256)".encode()).hex(),
    "BountyJoined": "0x" + __import__("eth_hash.auto", fromlist=["keccak"]).keccak(
        "BountyJoined(uint256,address,uint256,uint256,uint256)".encode()).hex(),
    "BountyCancelled": "0x" + __import__("eth_hash.auto", fromlist=["keccak"]).keccak(
        "BountyCancelled(uint256,address,uint256,uint256)".encode()).hex(),
    "WithdrawFromOpenBounty": "0x" + __import__("eth_hash.auto", fromlist=["keccak"]).keccak(
        "WithdrawFromOpenBounty(uint256,address,uint256,uint256,uint256)".encode()).hex(),
    "RefundClaimed": "0x" + __import__("eth_hash.auto", fromlist=["keccak"]).keccak(
        "RefundClaimed(uint256,address,uint256,uint256)".encode()).hex(),
    "ClaimAccepted": "0x" + __import__("eth_hash.auto", fromlist=["keccak"]).keccak(
        "ClaimAccepted(uint256,uint256,address,address,uint256,uint256,uint256,uint256)".encode()).hex(),
    "Transfer": "0x" + __import__("eth_hash.auto", fromlist=["keccak"]).keccak(
        "Transfer(address,address,uint256)".encode()).hex(),
}


def topic_name(t0):
    for name, h in TOPIC.items():
        if t0.lower() == h:
            return name
    return None


def addr_of_topic(t):
    return "0x" + t[-40:].lower()


def words(data_hex):
    h = data_hex[2:] if data_hex.startswith("0x") else data_hex
    return [h[i:i + 64] for i in range(0, len(h), 64)]


def uint(w):
    return int(w, 16)


def wei_to_eth(wei):
    """Exact decimal conversion (no float)."""
    if wei == 0:
        return "0"
    neg = wei < 0
    wei = abs(wei)
    s = str(wei).rjust(19, "0")
    whole, frac = s[:-18], s[-18:]
    frac = frac.rstrip("0")
    return ("-" if neg else "") + whole + ("." + frac if frac else "")


def sort_logs(logs):
    # normalize source order (Blockscout pages come newest-first) so that
    # owner overwrites process chronologically
    return sorted(
        logs,
        key=lambda lg: (int(lg["blockNumber"], 16), int(lg.get("logIndex", "0x0"), 16)),
    )


def build_chain(chain):
    with open(os.path.join(DATA, f"raw_logs_{chain}.json")) as f:
        raw = json.load(f)
    raw["core"] = sort_logs(raw["core"])
    raw["nft"] = sort_logs(raw["nft"])

    bounties = {}          # id -> {issuer, created_amount, open}
    joined = defaultdict(int)      # (bounty_id, wallet) -> wei
    withdrawn = defaultdict(int)   # (bounty_id, wallet) -> wei
    accepted = {}          # bounty_id -> {claim_issuer, amount, payout, fee}
    cancelled = set()
    refunds = defaultdict(int)
    core = CORE[chain]

    for lg in raw["core"]:
        name = topic_name(lg["topics"][0])
        if name is None:
            continue
        w = words(lg.get("data", "0x"))
        if name == "BountyCreated":
            bid = uint(lg["topics"][1])
            issuer = addr_of_topic(lg["topics"][2])
            bounties[bid] = {"issuer": issuer, "created_amount": uint(w[2]),
                             "open": uint(w[4]) == 1, "block": int(lg["blockNumber"], 16)}
        elif name == "BountyJoined":
            bid = uint(lg["topics"][1])
            p = addr_of_topic(lg["topics"][2])
            joined[(bid, p)] += uint(w[0])
        elif name == "WithdrawFromOpenBounty":
            bid = uint(lg["topics"][1])
            p = addr_of_topic(lg["topics"][2])
            withdrawn[(bid, p)] += uint(w[0])
        elif name == "BountyCancelled":
            bid = uint(lg["topics"][1])
            cancelled.add(bid)
        elif name == "RefundClaimed":
            bid = uint(lg["topics"][1])
            p = addr_of_topic(lg["topics"][2])
            refunds[(bid, p)] += uint(w[0])
        elif name == "ClaimAccepted":
            bid = uint(lg["topics"][1])
            claim_issuer = addr_of_topic(lg["topics"][3])
            accepted[bid] = {
                "claim_issuer": claim_issuer,
                "bounty_issuer": addr_of_topic("0x" + w[0][-40:]),
                "amount": uint(w[1]),
                "payout": uint(w[2]),
                "fee": uint(w[3]),
                "block": int(lg["blockNumber"], 16),
            }

    # ---- per-wallet wei sums (integers, no float error) ----
    joined_by_bounty = defaultdict(list)
    for (bid, p), amt in joined.items():
        joined_by_bounty[bid].append((p, amt))
    withdrawn_by_bounty = defaultdict(list)
    for (bid, p), amt in withdrawn.items():
        withdrawn_by_bounty[bid].append((p, amt))
    refunds_by_bounty = defaultdict(list)
    for (bid, p), amt in refunds.items():
        refunds_by_bounty[bid].append((p, amt))

    paid = defaultdict(int)
    earned = defaultdict(int)
    paid_rows = []   # audit trail
    for bid, b in bounties.items():
        if bid not in accepted or bid in cancelled:
            continue
        contribs = defaultdict(int)
        contribs[b["issuer"]] += b["created_amount"]
        # open-bounty joiner amounts (issuer cannot join its own bounty per contract)
        for p, amt in joined_by_bounty.get(bid, []):
            contribs[p] += amt
        for p, amt in withdrawn_by_bounty.get(bid, []):
            contribs[p] -= amt
        for p, amt in refunds_by_bounty.get(bid, []):
            contribs[p] -= amt
            print(f"[{chain}] WARN refund on accepted bounty {bid} for {p}: {amt}")
        for p, amt in contribs.items():
            if amt != 0:
                paid[p] += amt
                paid_rows.append({"bounty_id": bid, "wallet": p, "wei": amt})
        a = accepted[bid]
        earned[a["claim_issuer"]] += a["amount"]

    # ---- NFT current ownership ----
    owners = {}  # token_id -> owner
    for lg in raw["nft"]:
        # only ERC-721 Transfer logs; other events (e.g. ERC-4906 MetadataUpdate)
        # are ignored — some arrive with null topics from Blockscout
        if len(lg["topics"]) < 3 or lg["topics"][0].lower() != TOPIC["Transfer"].lower():
            continue
        # deployed ERC-721s index tokenId as a 4th topic (empty data); older
        # standard puts it in data — support both shapes
        if len(lg["topics"]) >= 4 and isinstance(lg["topics"][3], str):
            tid = uint(lg["topics"][3])
        else:
            tid = uint(words(lg.get("data", "0x"))[0])
        frm = addr_of_topic(lg["topics"][1])
        to = addr_of_topic(lg["topics"][2])
        owners[tid] = to
    nft_counts = defaultdict(int)
    escrow_count = 0
    for tid, owner in owners.items():
        if owner == core:
            escrow_count += 1
        elif owner != "0x" + "0" * 40:
            nft_counts[owner] += 1

    stats = {
        "bounties_total": len(bounties),
        "bounties_accepted": len(accepted),
        "bounties_cancelled": len(cancelled & set(bounties.keys())),
        "nft_tokens_seen": len(owners),
        "nft_held_by_protocol_escrow": escrow_count,
        "gross_accepted_wei": sum(a["amount"] for a in accepted.values()),
        "fees_wei": sum(a["fee"] for a in accepted.values()),
        "paid_wei": sum(paid.values()),
        "earned_wei": sum(earned.values()),
    }
    return {
        "paid": dict(paid), "earned": dict(earned), "nft_counts": dict(nft_counts),
        "escrow_count": escrow_count, "stats": stats, "paid_rows": paid_rows,
        "accepted": accepted, "bounties": bounties,
    }


def main():
    per_chain = {}
    for chain in CHAINS:
        per_chain[chain] = build_chain(chain)
        print(f"[{chain}] {per_chain[chain]['stats']}")

    wallets = set()
    for chain in CHAINS:
        d = per_chain[chain]
        wallets |= set(d["paid"]) | set(d["earned"]) | set(d["nft_counts"])
    wallets = sorted(wallets)

    header = [
        "wallet_address",
        "eth_paid_mainnet", "eth_paid_arbitrum", "eth_paid_base", "eth_paid_total",
        "eth_earned_mainnet", "eth_earned_arbitrum", "eth_earned_base", "eth_earned_total",
        "nfts_held_mainnet", "nfts_held_arbitrum", "nfts_held_base", "nfts_held_total",
    ]
    lines = [",".join(header)]
    for wlt in wallets:
        paid = [per_chain[c]["paid"].get(wlt, 0) for c in CHAINS]
        earned = [per_chain[c]["earned"].get(wlt, 0) for c in CHAINS]
        nfts = [per_chain[c]["nft_counts"].get(wlt, 0) for c in CHAINS]
        row = [wlt]
        row += [wei_to_eth(v) for v in paid] + [wei_to_eth(sum(paid))]
        row += [wei_to_eth(v) for v in earned] + [wei_to_eth(sum(earned))]
        row += [str(v) for v in nfts] + [str(sum(nfts))]
        lines.append(",".join(row))

    csv_path = os.path.join(OUT, "poidh_v3_wallet_data.csv")
    with open(csv_path, "w", newline="") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {csv_path}: {len(wallets)} wallets")

    # ---- reconciliation ----
    recon = {"by_chain": {}, "total": {}}
    for chain in CHAINS:
        s = per_chain[chain]["stats"]
        recon["by_chain"][chain] = {
            "bounties_created": s["bounties_total"],
            "bounties_accepted": s["bounties_accepted"],
            "bounties_cancelled": s["bounties_cancelled"],
            "sum_eth_paid_contributors": wei_to_eth(s["paid_wei"]),
            "sum_eth_earned_winners_gross": wei_to_eth(s["earned_wei"]),
            "gross_accepted_amount_wei": s["gross_accepted_wei"],
            "protocol_fees_wei": s["fees_wei"],
            "match_paid_vs_earned": s["paid_wei"] == s["earned_wei"],
            "nfts_held_by_protocol_escrow": s["nft_held_by_protocol_escrow"],
        }
        print(f"[{chain}] recon match: {s['paid_wei'] == s['earned_wei']} "
              f"paid={s['paid_wei']} earned={s['earned_wei']}")
    t_paid = sum(per_chain[c]["stats"]["paid_wei"] for c in CHAINS)
    t_earned = sum(per_chain[c]["stats"]["earned_wei"] for c in CHAINS)
    t_gross = sum(per_chain[c]["stats"]["gross_accepted_wei"] for c in CHAINS)
    t_fee = sum(per_chain[c]["stats"]["fees_wei"] for c in CHAINS)
    recon["total"] = {
        "sum_eth_paid": wei_to_eth(t_paid), "sum_eth_earned": wei_to_eth(t_earned),
        "match": t_paid == t_earned, "gross_accepted_wei": t_gross, "fees_wei": t_fee,
        "wallets": len(wallets),
    }
    print(f"TOTAL match: {t_paid == t_earned}, paid={t_paid}")
    with open(os.path.join(DATA, "reconciliation.json"), "w") as f:
        json.dump(recon, f, indent=2)

    # keep per-chain aggregates for README/chart
    with open(os.path.join(DATA, "chain_stats.json"), "w") as f:
        json.dump({c: per_chain[c]["stats"] for c in CHAINS}, f, indent=2)

    # top earners/payers for the chart
    tops = sorted(wallets, key=lambda w: -(sum(per_chain[c]["earned"].get(w, 0) for c in CHAINS) +
                                            sum(per_chain[c]["paid"].get(w, 0) for c in CHAINS)))[:20]
    chart = {
        "wallets": tops,
        "paid": [[wei_to_eth(per_chain[c]["paid"].get(w, 0)) for c in CHAINS] for w in tops],
        "earned": [[wei_to_eth(per_chain[c]["earned"].get(w, 0)) for c in CHAINS] for w in tops],
        "nfts": [[per_chain[c]["nft_counts"].get(w, 0) for c in CHAINS] for w in tops],
        "chain_totals": {c: {
            "paid": wei_to_eth(per_chain[c]["stats"]["paid_wei"]),
            "earned": wei_to_eth(per_chain[c]["stats"]["earned_wei"]),
        } for c in CHAINS},
        "escrow_nfts": {c: per_chain[c]["escrow_count"] for c in CHAINS},
    }
    with open(os.path.join(DATA, "chart_data.json"), "w") as f:
        json.dump(chart, f, indent=2)


if __name__ == "__main__":
    main()
