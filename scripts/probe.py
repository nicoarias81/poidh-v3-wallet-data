"""Probe each public RPC's eth_getLogs max range for our filtered query."""
import sys
sys.path.insert(0, "scripts")
from rpc import CHAINS
import requests, time

CORE = {c: CHAINS[c]["core"] for c in CHAINS}

def probe(url, address, lo, spans):
    results = {}
    for span in spans:
        try:
            r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
                                         "params": [{"address": address,
                                                     "fromBlock": hex(lo),
                                                     "toBlock": hex(lo + span - 1)}]},
                              timeout=45, headers={"Content-Type": "application/json"})
            d = r.json()
            if "error" in d:
                results[span] = f"ERR {str(d['error'].get('message',''))[:60]}"
                break  # assume smaller spans also constrained similarly
            else:
                results[span] = f"OK ({len(d['result'])} logs)"
                time.sleep(0.3)
        except Exception as e:
            results[span] = f"EXC {type(e).__name__}"
            break
    return results

for chain in ["mainnet", "arbitrum", "base"]:
    print(f"### {chain}")
    for url in CHAINS[chain]["rpcs"]:
        try:
            r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber",
                                         "params": []}, timeout=15)
            latest = int(r.json()["result"], 16)
        except Exception as e:
            print(f"  {url}: dead ({e})")
            continue
        lo = latest - 50_000
        res = probe(url, CHAINS[chain]["core"], lo, [50, 1_000, 10_000, 50_000, 100_000, 500_000, 2_000_000])
        print(f"  {url}: {res}")
