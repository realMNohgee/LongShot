<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-orange?style=for-the-badge" alt="version">
  <img src="https://img.shields.io/badge/python-3.10+-blue?style=for-the-badge" alt="python">
  <img src="https://img.shields.io/badge/deps-zero-success?style=for-the-badge" alt="zero dependencies">
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=for-the-badge" alt="license">
</p>

<h1 align="center">
  <span style="color:#F7931A">🎰 Long</span><span style="color:#FFD700">Shot</span>
</h1>
<h3 align="center">Bitcoin Solo Lottery Miner</h3>

<p align="center">
  <i>Solo-mine Bitcoin on your CPU. Connect to a Stratum pool.<br>
  Each hash is a lottery ticket. 99.99999% you earn nothing.<br>
  But one lucky hash? <strong>3.125 ₿.</strong></i>
</p>

---

## 🎰 The Long Shot

Your laptop computes SHA-256d hashes and submits them to a public solo mining pool. The entire Bitcoin network does 650 exahashes per second. Your MacBook does about 12 megahashes. That's 54 million times slower.

But solo mining doesn't split the reward. If — against all odds — your CPU produces a valid block hash, you collect the full **3.125 BTC** reward. Currently worth over $300,000.

It's a lottery ticket that costs ~$0.10/day in electricity instead of $2 at the gas station.

## 🚀 Quick Start

```bash
# See your actual hashrate and odds
python longshot.py odds

# Start mining on a public solo pool
python longshot.py mine
```

## 📊 Live Dashboard

LongShot draws a real-time terminal dashboard that mirrors the web UI:

- **4-card stats grid** — Hashrate, Total Hashes, Session Time, Power Cost
- **Live hash stream** — Real SHA-256d hashes as they're computed
- **Odds calculator** — Per block, day, week, month, year
- **Prize display** — Current BTC reward + USD value
- **Electricity tracker** — What this is actually costing you

## ⛏ How It Works

1. **Connect** — Links to a public Stratum solo pool (ckpool, zsolo, public-pool)
2. **Get work** — Pool sends a block template. Your job: find a nonce that makes the hash meet the target.
3. **Hash** — Your CPU runs `SHA256(SHA256(header))` in a tight loop. Each hash is a lottery ticket.
4. **Submit** — Every ~500K hashes, a share is submitted to prove you're working.
5. **Win or lose** — 99.99999% of the time: nothing. 0.00001%: 3.125 BTC.

## 🎯 Real Talk

| Estimate | Value |
|----------|-------|
| Your hashrate | ~1-12 MH/s (CPU-dependent) |
| Network hashrate | ~650 EH/s |
| Your share | ~0.000000000002% |
| Odds per day | 1 in several trillion |
| Odds per year | 1 in several trillion |
| Electricity cost | ~$37/year |
| Reward if you hit | 3.125 ₿ (~$300K+) |

**You will almost certainly never find a block.** This is a novelty tool. A conversation starter. A way to understand how Bitcoin mining actually works at the protocol level. But if you leave it running while you sleep... you never know.

## 🔧 Requirements

- Python 3.10+
- An internet connection (connects to public Stratum pools)
- That's it. No dependencies. Pure stdlib.

## 📄 License

MIT

---

<p align="center">
  <sub>Part of the <a href="https://hermtica.com">Hermtica</a> marketplace · Free</sub>
</p>
