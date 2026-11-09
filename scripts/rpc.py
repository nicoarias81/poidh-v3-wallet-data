"""Shared RPC client with multi-provider fallback for the poidh v3 dataset build.

No paid keys; only public RPC endpoints.
"""
import json
import time
import urllib.request

CHAINS = {
    "mainnet": {
        "chain_id": 1,
        "core": "0xe731dfadbff20542e10d09d26fc71445c70d4232",
        "nft": "0x9c5f45d5e1382e4058d334d93c6c01442012a4d9",
        "rpcs": [
            "https://ethereum-rpc.publicnode.com",
            "https://rpc.mevblocker.io",
            "https://eth.drpc.org",
        ],
    },
    "arbitrum": {
        "chain_id": 42161,
        "core": "0x5555fa783936c260f77385b4e153b9725fef1719",
        "nft": "0x27e117cc9a8da363442e7bd0618939e3eeeacf6a",
        "rpcs": [
            "https://arb1.arbitrum.io/rpc",
            "https://arbitrum-one-rpc.publicnode.com",
        ],
    },
    "base": {
        "chain_id": 8453,
        "core": "0x5555fa783936c260f77385b4e153b9725fef1719",
        "nft": "0x27e117cc9a8da363442e7bd0618939e3eeeacf6a",
        "rpcs": [
            "https://base.drpc.org",
            "https://base-rpc.publicnode.com",
            "https://mainnet.base.org",
        ],
    },
}


class Rpc:
    def __init__(self, chain: str):
        self.name = chain
        self.urls = list(CHAINS[chain]["rpcs"])
        self.best = None  # sticky last-successful provider
        self.range_fail = {}  # url -> smallest known-failing getLogs range size
        self._id = 0

    def _post(self, url, payload, timeout=60):
        import requests

        r = requests.post(
            url, json=payload, timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        # providers like drpc answer 4xx with a JSON-RPC error body: parse it
        # so the error text reaches the transient/range classification
        if r.status_code >= 400:
            try:
                body = r.json()
                if isinstance(body, dict) and "error" in body:
                    return body
            except ValueError:
                pass
            r.raise_for_status()
        return r.json()

    def call(self, method, params, retries=3, timeout=60):
        """JSON-RPC call, rotating providers on failure."""
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        last_err = None
        for attempt in range(retries):
            for u in self.urls:
                try:
                    resp = self._post(u, payload, timeout=timeout)
                    if "error" in resp:
                        # rotate: other providers may still serve this call
                        last_err = RpcError(resp["error"])
                        time.sleep(0.5)
                        continue
                    if "result" not in resp:
                        last_err = RpcError(f"no result: {resp}")
                        continue
                    self.best = u
                    return resp["result"]
                except RpcError:
                    raise
                except Exception as e:  # transport error -> try next provider
                    last_err = e
                    time.sleep(0.5)
            time.sleep(10 * (attempt + 1))
        raise RuntimeError(f"{self.name}: {method} failed on all providers: {last_err}")

    def batch(self, calls):
        """Batch JSON-RPC calls; falls back to per-call on batch failure."""
        self._id += 1
        payload = [
            {"jsonrpc": "2.0", "id": self._id + i, "method": m, "params": p}
            for i, (m, p) in enumerate(calls)
        ]
        for u in self.urls:
            try:
                resp = self._post(u, payload, timeout=120)
                if isinstance(resp, dict) and "error" in resp:
                    raise RpcError(resp["error"])
                by_id = {r["id"]: r for r in resp}
                out = []
                for i in range(len(calls)):
                    r = by_id[self._id + i]
                    if "error" in r:
                        raise RpcError(r["error"])
                    out.append(r["result"])
                self.url = u
                return out
            except RpcError:
                raise
            except Exception:
                continue
        # last resort: sequential
        return [self.call(m, p) for m, p in calls]

    def latest_block(self):
        return int(self.call("eth_blockNumber", []), 16)

    def block_timestamp(self, block):
        if isinstance(block, int):
            block = hex(block)
        return int(self.call("eth_getBlockByNumber", [block, False])["timestamp"], 16)

    def get_logs(self, address, topics, from_block, to_block):
        params = {
            "address": address,
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }
        if topics:
            params["topics"] = topics
        # Some JSON-RPC errors are provider-specific (range limits, pruning,
        # rate limits) -> rotate providers before giving up / splitting.
        span = to_block - from_block + 1
        # order: sticky best first, then providers not known to fail at this span
        rest = [u for u in self.urls if u != self.best]
        rest.sort(key=lambda u: 0 if self.range_fail.get(u, 0) < span else 1)
        order = ([self.best] if self.best else []) + rest
        last_err = None
        for u in order:
            try:
                resp = self._post(u, {
                    "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs", "params": [params]
                }, timeout=180)
                if "error" in resp:
                    raise RpcError(resp["error"])
                self.best = u
                return resp["result"]
            except RpcError as e:
                msg = str(e).lower()
                last_err = e
                rangey = any(k in msg for k in ("range", "limit", "too many", "exceeds"))
                if rangey:
                    self.range_fail[u] = min(self.range_fail.get(u, 1 << 62), span)
                transient = any(k in msg for k in
                                ("range", "limit", "pruned", "rate", "429", "timeout", "too many",
                                 "unavailable", "busy", "reset", "eof", "timed out",
                                 "usage limit", "plan", "archive", "quota"))
                if not transient:
                    raise
                time.sleep(0.3)
            except Exception as e:
                last_err = e
                time.sleep(0.3)
        raise last_err


class RpcError(Exception):
    pass


def topic_of(sig: str) -> str:
    from eth_hash.auto import keccak  # noqa
