"""Fetch all poidh v3 protocol + NFT logs up to the snapshot cutoff.

Outputs data/raw_logs_<chain>.json with every relevant event, plus
data/snapshot.json documenting block numbers per chain.

Usage: python scripts/fetch.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from rpc import CHAINS, Rpc  # noqa: E402
from eth_hash.auto import keccak  # noqa: E402

CUTOFF_TS = None  # 2026-11-08T03:59:54Z, computed below
import calendar  # noqa: E402

CUTOFF_TS = calendar.timegm(time.strptime("2026-11-08T03:59:54Z", "%Y-%m-%dT%H:%M:%SZ"))

def K(sig):
    return "0x" + keccak(sig.encode()).hex()


EVENT_TOPICS = {
    "BountyCreated": K("BountyCreated(uint256,address,string,string,uint256,uint256,bool,uint256)"),
    "ClaimCreated": K("ClaimCreated(uint256,address,uint256,address,string,string,uint256,string,uint256)"),
    "ClaimAccepted": K("ClaimAccepted(uint256,uint256,address,address,uint256,uint256,uint256,uint256)"),
    "BountyJoined": K("BountyJoined(uint256,address,uint256,uint256,uint256)"),
    "BountyCancelled": K("BountyCancelled(uint256,address,uint256,uint256)"),
    "WithdrawFromOpenBounty": K("WithdrawFromOpenBounty(uint256,address,uint256,uint256,uint256)"),
    "RefundClaimed": K("RefundClaimed(uint256,address,uint256,uint256)"),
}
NFT_TRANSFER = K("Transfer(address,address,uint256)")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
os.makedirs(DATA, exist_ok=True)

# Rough first-guess bounds: (earliest_date_str) per chain for contract deployment.
DEPLOY_GUESS = {"mainnet": "2026-05-10", "arbitrum": "2026-01-15", "base": "2026-01-15"}


def ts_to_date(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def find_block_at_or_before(rpc: Rpc, ts_target, lo, hi):
    """Largest block with timestamp <= ts_target in [lo, hi]."""
    while lo < hi:
        mid = (lo + hi + 1) // 2
        t = rpc.block_timestamp(mid)
        if t <= ts_target:
            lo = mid
        else:
            hi = mid - 1
    return lo


def find_first_log_block(rpc: Rpc, address, topic0, lo, hi):
    """Binary search smallest block whose [b, hi] window contains the log.
    Uses the monotone property: logs exist in [b, hi] iff any log >= b."""
    # find any log first
    probe = rpc.get_logs(address, [topic0], lo, hi)
    if not probe:
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        try:
            found = rpc.get_logs(address, [topic0], mid, hi)
        except Exception:
            # window too large: narrow from the right using a smaller probe
            mid = (lo + hi) // 2
            found = rpc.get_logs(address, [topic0], mid, (mid + hi) // 2)
        if found:
            hi = int(found[0]["blockNumber"], 16)
            lo = max(lo, hi) if hi == int(found[0]["blockNumber"], 16) else lo
            # ensure lo can still contain it
            if int(found[0]["blockNumber"], 16) > lo:
                pass
        else:
            lo = mid + 1
    return lo


def fetch_logs_chunked(rpc: Rpc, address, topics, from_block, to_block, tag):
    """Linear scan with adaptive chunk size: grow on success, halve on error."""
    out = []
    a = from_block
    chunk = 10_000  # conservative start; adapts to the friendliest provider
    max_chunk = 100_000
    min_chunk = 2_000
    while a <= to_block:
        b = min(a + chunk - 1, to_block)
        try:
            logs = rpc.get_logs(address, topics, a, b)
        except Exception as e:
            if chunk > min_chunk:
                chunk = max(min_chunk, chunk // 4)
                print(f"    shrink chunk -> {chunk} at [{a},{b}] ({tag}): {str(e)[:100]}", flush=True)
            else:
                # all providers are rate-limited: back off and retry, never go below min_chunk
                print(f"    rate-limited at [{a},{b}] ({tag}); backing off 30s: {str(e)[:80]}", flush=True)
                time.sleep(30)
            continue
        out.extend(logs)
        if chunk < max_chunk:
            # empty ranges are cheap: accelerate fast; log-dense ranges grow slower
            chunk = min(chunk * (8 if not logs else 2), max_chunk)
        a = b + 1
        if (a - from_block) // max(chunk, 1) % 25 == 0:
            print(f"    [{tag}] progress {a} (chunk {chunk}, {len(out)} logs)", flush=True)
        time.sleep(0.05)
    return out


def main():
    snapshot = {"cutoff_ts": CUTOFF_TS, "cutoff_iso": ts_to_date(CUTOFF_TS), "chains": {}}
    snap_path = os.path.join(DATA, "snapshot.json")
    if os.path.exists(snap_path):
        with open(snap_path) as f:
            snapshot["chains"] = json.load(f).get("chains", {})
    for chain in ["mainnet", "arbitrum", "base"]:
        cfg = CHAINS[chain]
        raw_path = os.path.join(DATA, f"raw_logs_{chain}.json")
        if os.path.exists(raw_path) and chain in snapshot["chains"]:
            print(f"[{chain}] already fetched, skipping")
            continue
        rpc = Rpc(chain)
        latest = rpc.latest_block()
        print(f"[{chain}] latest block {latest}")
        guess_ts = calendar.timegm(time.strptime(DEPLOY_GUESS[chain], "%Y-%m-%d"))
        # mainnet providers prune pre-15500000 history; deployment is far above it
        lo = 15_500_000 if chain == "mainnet" else 1
        deploy_block_guess = find_block_at_or_before(rpc, guess_ts, lo, latest)
        cutoff_block = find_block_at_or_before(rpc, CUTOFF_TS, lo, latest)
        cutoff_block_ts = rpc.block_timestamp(cutoff_block)
        print(f"[{chain}] deploy guess block {deploy_block_guess}, cutoff block {cutoff_block} "
              f"(ts {ts_to_date(cutoff_block_ts)})")

        all_topics = list(EVENT_TOPICS.values())
        # Fetch core + NFT logs in parallel with independent Rpc clients.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(2) as ex:
            fut_core = ex.submit(fetch_logs_chunked, Rpc(chain), cfg["core"],
                                 [all_topics], deploy_block_guess, cutoff_block, "core")
            fut_nft = ex.submit(fetch_logs_chunked, Rpc(chain), cfg["nft"],
                                [[NFT_TRANSFER]], deploy_block_guess, cutoff_block, "nft")
            logs = fut_core.result()
            nft_logs = fut_nft.result()
        by_topic = {name: 0 for name in EVENT_TOPICS}
        for lg in logs:
            t0 = lg["topics"][0].lower()
            for name, h in EVENT_TOPICS.items():
                if t0 == h.lower():
                    by_topic[name] += 1
        print(f"[{chain}] core logs: {len(logs)} -> {by_topic}")

        first = min((int(l["blockNumber"], 16) for l in logs), default=None)
        print(f"[{chain}] first core log block: {first}")
        print(f"[{chain}] nft Transfer logs: {len(nft_logs)}")

        with open(os.path.join(DATA, f"raw_logs_{chain}.json"), "w") as f:
            json.dump({"core": logs, "nft": nft_logs}, f)

        snapshot["chains"][chain] = {
            "chain_id": cfg["chain_id"],
            "core": cfg["core"],
            "nft": cfg["nft"],
            "deploy_guess_block": deploy_block_guess,
            "first_bounty_block": first,
            "cutoff_block": cutoff_block,
            "cutoff_block_ts": cutoff_block_ts,
            "cutoff_block_iso": ts_to_date(cutoff_block_ts),
            "latest_block_at_fetch": latest,
            "core_log_counts": by_topic,
            "nft_transfer_count": len(nft_logs),
            "rpc_used": rpc.url,
        }
        with open(os.path.join(DATA, "snapshot.json"), "w") as f:
            json.dump(snapshot, f, indent=2)
        time.sleep(0.5)
    print("done")


if __name__ == "__main__":
    main()
