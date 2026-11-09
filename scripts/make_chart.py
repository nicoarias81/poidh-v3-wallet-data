"""Render the claim visualization PNG from chart_data.json."""
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
OUT = os.path.join(ROOT, "chart")
os.makedirs(OUT, exist_ok=True)

with open(os.path.join(ROOT, "data", "chart_data.json")) as f:
    cd = json.load(f)

wallets = cd["wallets"]
short = [w[:6] + "…" + w[-4:] for w in wallets]
paid = np.array([[float(x) for x in row] for row in cd["paid"]])      # per chain
earned = np.array([[float(x) for x in row] for row in cd["earned"]])
nfts = np.array(cd["nfts"])
totals_paid = paid.sum(axis=1)
totals_earned = earned.sum(axis=1)
totals_nfts = nfts.sum(axis=1)

y = np.arange(len(wallets))[::-1]

fig, axes = plt.subplots(1, 3, figsize=(16, 0.42 * len(wallets) + 3.2), sharey=True)
fig.suptitle("poidh v3 — top 20 wallets by combined activity (all chains, snapshot 2026-11-08T03:59:54Z)",
             fontsize=13, fontweight="bold")

CHAIN_COLORS = ["#627eea", "#28a0f0", "#0052ff"]

ax = axes[0]
ax.barh(y, totals_paid, color="#f7931a", edgecolor="white", linewidth=0.5)
ax.set_title("ETH paid (contributions to accepted bounties)", fontsize=10)
ax.set_xlabel("ETH", fontsize=9)
ax.tick_params(labelsize=8)
ax.grid(axis="x", alpha=0.3)

ax = axes[1]
ax.barh(y, totals_earned, color="#16a34a")
ax.set_title("ETH earned (gross, before 2.5% fee)", fontsize=10)
ax.set_xlabel("ETH", fontsize=9)
ax.tick_params(labelsize=8)
ax.grid(axis="x", alpha=0.3)

ax = axes[2]
ax.barh(y, totals_nfts, color="#7c3aed")
ax.set_title("poidh claim NFTs held (current ownership)", fontsize=10)
ax.set_xlabel("NFTs", fontsize=9)
ax.tick_params(labelsize=8)
ax.grid(axis="x", alpha=0.3)

for ax_ in axes:
    ax_.set_yticks(y)
    ax_.set_yticklabels(short, fontsize=8, family="monospace")

# per-chain stacked detail as inset text on the paid chart
info = "\n".join(
    f"{c}: paid {float(cd['chain_totals'][c]['paid']):.4f} / earned {float(cd['chain_totals'][c]['earned']):.4f} ETH"
    for c in ["mainnet", "arbitrum", "base"]
)
escrow = cd["escrow_nfts"]
info2 = info + f"\nclaim NFTs held by protocol escrow: " + ", ".join(
    f"{c}: {escrow[c]}" for c in ["mainnet", "arbitrum", "base"]
)
fig.text(0.01, 0.01, info2, fontsize=8, family="monospace", va="bottom", color="#444")

plt.tight_layout(rect=(0, 0.045, 1, 0.96))
path = os.path.join(OUT, "poidh_v3_wallets.png")
plt.savefig(path, dpi=150, facecolor="white")
print("wrote", path)
