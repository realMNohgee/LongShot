#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════╗
║           🎰  LONGSHOT  v1.0                    ║
║        Bitcoin Solo Lottery Miner               ║
╚══════════════════════════════════════════════════╝

Solo-mine Bitcoin blocks via Stratum protocol.
Treat mining like a lottery ticket — a few cents of
electricity for a 1-in-5-quadrillion shot at 3.125 ₿.

Zero dependencies. Pure Python stdlib.
"""

import sys
import json
import socket
import hashlib
import struct
import time
import random
import threading
import subprocess
import math
from datetime import datetime, timedelta

# ── Styling ───────────────────────────────────────────────────
RST  = "\033[0m"
BLD  = "\033[1m"
DIM  = "\033[2m"
ORANGE = "\033[38;5;214m"
GOLD   = "\033[33m"
GRN    = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYN    = "\033[36m"
WHT    = "\033[97m"
GRAY   = "\033[90m"

# ── Constants ─────────────────────────────────────────────────
# Solo mining pools (public, no registration needed)
SOLO_POOLS = [
    ("solo.ckpool.org", 3333),   # ckpool solo — most popular
    ("btc.zsolo.bid", 3333),     # zsolo
    ("public-pool.io", 3333),    # public-pool
]

BITCOIN_REWARD = 3.125  # BTC per block (post-2024 halving)
NETWORK_HASHRATE = 650_000_000_000_000_000_000  # ~650 EH/s (July 2026 estimate)

# ── Stratum Protocol ──────────────────────────────────────────
def jsonrpc(method, params=None):
    return json.dumps({
        "id": random.randint(1, 999999),
        "method": method,
        "params": params or []
    }) + "\n"

def recv_line(sock):
    buf = b""
    while True:
        chunk = sock.recv(1)
        if not chunk:
            return None
        buf += chunk
        if chunk == b"\n":
            return buf.decode().strip()

class StratumClient:
    def __init__(self, host, port, username="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", password="x"):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sock = None
        self.extranonce1 = None
        self.extranonce2_size = 4
        self.job = None
        self.submitted = 0
        self.accepted = 0
        self.running = False
        self.hashrate_samples = []
        self.recent_hashes = []  # for live hash stream display
        self.start_time = None

    def connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=15)
            return True
        except Exception as e:
            return False

    def subscribe(self):
        # Subscribe to mining
        self.sock.sendall(jsonrpc("mining.subscribe", ["longshot/1.0"]).encode())
        resp = json.loads(recv_line(self.sock))
        if resp.get("error"):
            return False
        self.extranonce1 = resp["result"][1]
        self.extranonce2_size = resp["result"][2]
        # Server immediately sends set_difficulty + first notify after subscribe.
        # Consume them so they don't interfere with the authorize response.
        self.sock.settimeout(2)
        try:
            while True:
                line = recv_line(self.sock)
                if not line:
                    break
                data = json.loads(line)
                if data.get("method") == "mining.notify":
                    # Store first job
                    params = data["params"]
                    self.job = {
                        "job_id": params[0], "prevhash": params[1],
                        "coinb1": params[2], "coinb2": params[3],
                        "merkle_branches": params[4], "version": params[5],
                        "nbits": params[6], "ntime": params[7],
                        "clean_jobs": params[8] if len(params) > 8 else True
                    }
                    break
        except (socket.timeout, json.JSONDecodeError):
            pass
        finally:
            self.sock.settimeout(None)
        return True

    def authorize(self):
        self.sock.sendall(jsonrpc("mining.authorize", [self.username, self.password]).encode())
        resp = json.loads(recv_line(self.sock))
        # ckpool returns result=True on success, some pools return error=None
        if resp.get("result") == True:
            return True
        if resp.get("error") is None and resp.get("result") is not False:
            return True
        # Debug
        sys.stderr.write(f"\n  {YELLOW}Auth response: {json.dumps(resp)}{RST}\n")
        return False

    def get_job(self):
        """Wait for and parse a mining job."""
        while self.running:
            line = recv_line(self.sock)
            if not line:
                return None
            try:
                data = json.loads(line)
            except:
                continue

            if data.get("method") == "mining.notify":
                params = data["params"]
                self.job = {
                    "job_id": params[0],
                    "prevhash": params[1],
                    "coinb1": params[2],
                    "coinb2": params[3],
                    "merkle_branches": params[4],
                    "version": params[5],
                    "nbits": params[6],
                    "ntime": params[7],
                    "clean_jobs": params[8] if len(params) > 8 else True
                }
                return self.job

    def mine_loop(self, hashrate_callback=None):
        """Main mining loop."""
        self.running = True
        self.start_time = time.time()

        while self.running:
            job = self.get_job()
            if not job:
                break

            # Convert nbits to target
            target = nbits_to_target(job["nbits"])

            # Mine this job until we get a new one
            extranonce2 = 0
            hashes_this_job = 0
            job_start = time.time()

            while self.running:
                # Build coinbase
                coinbase = job["coinb1"] + self.extranonce1 + \
                          format(extranonce2, f"0{self.extranonce2_size * 2}x") + \
                          job["coinb2"]

                # Build merkle root
                merkle_root = double_sha256(bytes.fromhex(coinbase))
                for branch in job["merkle_branches"]:
                    merkle_root = double_sha256(merkle_root + bytes.fromhex(branch))

                # Build block header
                header = (
                    int(job["version"], 16).to_bytes(4, "little") +
                    bytes.fromhex(job["prevhash"])[::-1] +
                    merkle_root[::-1] +
                    int(job["ntime"], 16).to_bytes(4, "little") +
                    int(job["nbits"], 16).to_bytes(4, "little") +
                    b"\x00\x00\x00\x00"  # nonce placeholder
                )

                # Hash at nonce 0 as a baseline
                hash_result = double_sha256(header)
                hash_hex = hash_result[::-1].hex()
                hashes_this_job += 1
                self.submitted += 1

                # Store for live display
                self.recent_hashes.append(hash_hex)
                if len(self.recent_hashes) > 20:
                    self.recent_hashes = self.recent_hashes[-20:]

                # Check if hash meets target
                hash_int = int.from_bytes(hash_result[::-1], "big")
                if hash_int <= target:
                    # BLOCK FOUND!
                    return {
                        "type": "block",
                        "hash": hash_result[::-1].hex(),
                        "job_id": job["job_id"],
                        "extranonce2": format(extranonce2, f"0{self.extranonce2_size * 2}x"),
                        "ntime": job["ntime"],
                        "nonce": "00000000"
                    }

                extranonce2 += 1
                if extranonce2 >= (256 ** self.extranonce2_size):
                    break  # Job exhausted, get new one

                # Rate limit updates
                if hashes_this_job >= 500000:
                    elapsed = time.time() - job_start
                    rate = hashes_this_job / elapsed if elapsed > 0 else 0
                    self.hashrate_samples.append(rate)
                    if len(self.hashrate_samples) > 20:
                        self.hashrate_samples.pop(0)
                    self.accepted += 1
                    if hashrate_callback:
                        hashrate_callback(self.avg_hashrate(), self.submitted)
                    break

        self.running = False
        return None

    def avg_hashrate(self):
        if not self.hashrate_samples:
            return 0
        return sum(self.hashrate_samples) / len(self.hashrate_samples)

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass


# ── Crypto helpers ────────────────────────────────────────────
def double_sha256(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()

def nbits_to_target(nbits_hex):
    """Convert Bitcoin nbits to target integer."""
    nbits = int(nbits_hex, 16)
    exponent = nbits >> 24
    mantissa = nbits & 0x00ffffff
    return mantissa * (2 ** (8 * (exponent - 3)))

def format_hashrate(h):
    if h < 1000:
        return f"{h:.0f} H/s"
    elif h < 1_000_000:
        return f"{h/1000:.1f} KH/s"
    elif h < 1_000_000_000:
        return f"{h/1_000_000:.1f} MH/s"
    elif h < 1_000_000_000_000:
        return f"{h/1_000_000_000:.1f} GH/s"
    else:
        return f"{h/1_000_000_000_000:.1f} TH/s"


# ── Probability ────────────────────────────────────────────────
def odds_per_block(hashes_per_second):
    """Probability of finding a block in a single attempt."""
    # Each hash is a number 0 to 2^256-1
    # Target is roughly 2^256 / (difficulty)
    difficulty = 85_000_000_000_000  # Approx July 2026 difficulty
    target = (2**256 - 1) // difficulty
    prob_per_hash = target / (2**256)
    # For N hashes per ~10min block time
    hashes_per_block = hashes_per_second * 600
    return 1 - (1 - prob_per_hash) ** hashes_per_block

def format_odds(prob):
    """Human readable odds."""
    if prob == 0:
        return "∞"
    one_in = 1 / prob if prob > 0 else float('inf')
    if one_in < 1000:
        return f"1 in {one_in:,.0f}"
    elif one_in < 1_000_000:
        return f"1 in {one_in/1000:.0f}K"
    elif one_in < 1_000_000_000:
        return f"1 in {one_in/1_000_000:.0f}M"
    elif one_in < 1_000_000_000_000:
        return f"1 in {one_in/1_000_000_000:.0f}B"
    else:
        return f"1 in {one_in/1_000_000_000_000:.0f}T"


# ── Terminal UI (matches web demo layout) ──────────────────────
def clear():
    print("\033[2J\033[H", end="")

def strip_ansi(s):
    """Remove ANSI escape sequences to get visible length."""
    import re
    return re.sub(r'\033\[[0-9;]*m', '', str(s))

def pad_visible(text, width, align='left'):
    """Pad text accounting for ANSI codes."""
    visible = strip_ansi(text)
    padding = max(0, width - len(visible))
    if align == 'right':
        return ' ' * padding + text
    return text + ' ' * padding

def draw_full_box(title, lines, w=58):
    """Draw a full-width box with title and content lines, ANSI-safe."""
    out = [f"\n  {ORANGE}┌{'─' * w}┐{RST}"]
    out.append(f"  {ORANGE}│{RST} {pad_visible(f'{BLD}{title}{RST}', w-2)} {ORANGE}│{RST}")
    out.append(f"  {ORANGE}├{'─' * w}┤{RST}")
    for line in lines:
        out.append(f"  {ORANGE}│{RST} {pad_visible(line, w-2)} {ORANGE}│{RST}")
    out.append(f"  {ORANGE}└{'─' * w}┘{RST}")
    return "\n".join(out)

def draw_odds_row(label, odds_val, label2, val2):
    """Single row in the odds/prize cards. Each card is 30 chars total."""
    left = f"  {ORANGE}│{RST} {DIM}{pad_visible(label, 13)}{RST}{RED}{pad_visible(odds_val, 13, 'right')}{RST} {ORANGE}│{RST}"
    right = f"  {ORANGE}│{RST} {DIM}{pad_visible(label2, 13)}{RST}{pad_visible(val2, 13, 'right')}{RST} {ORANGE}│{RST}"
    return f"{left} {right}"

def print_dashboard(client, elapsed, btc_price=None):
    clear()
    hr = client.avg_hashrate()
    odds = odds_per_block(hr) if hr > 0 else 0
    daily_odds = 1 - (1 - odds) ** 144
    btc_value = BITCOIN_REWARD * (btc_price or 100000)
    
    kwh_used = (35 / 1000) * (elapsed / 3600)
    power_cost = kwh_used * 0.12
    daily_cost = 0.035 * 24 * 0.12
    
    # ═══ HEADER ═══
    print(f"""
{ORANGE}╔{'═' * 58}╗
║{RST}         {BLD}🎰  LONGSHOT{RST} — Bitcoin Lottery Miner        {ORANGE}║
║{RST}           {DIM}Solo mining. All or nothing.{RST}                  {ORANGE}║
╚{'═' * 58}╝{RST}""")

    # ═══ STATS GRID (4 cards) ═══
    hr_str = format_hashrate(hr)
    hashes_str = f"{client.submitted:,}"
    time_str = str(timedelta(seconds=int(elapsed)))
    cost_str = f"${power_cost:.4f}"
    
    # Row 1: Hashrate + Total Hashes
    print(f"\n  {ORANGE}┌{'─'*26}┐  ┌{'─'*26}┐{RST}")
    print(f"  {ORANGE}│{RST} {pad_visible(f'{GRAY}HASHRATE{RST}', 24)} {ORANGE}│{RST}  {ORANGE}│{RST} {pad_visible(f'{GRAY}TOTAL HASHES{RST}', 24)} {ORANGE}│{RST}")
    print(f"  {ORANGE}│{RST} {pad_visible(f'{ORANGE}{BLD}{hr_str}{RST}', 24)} {ORANGE}│{RST}  {ORANGE}│{RST} {pad_visible(f'{WHT}{hashes_str}{RST}', 24)} {ORANGE}│{RST}")
    print(f"  {ORANGE}│{RST} {pad_visible(f'{DIM}SHA-256d on CPU{RST}', 24)} {ORANGE}│{RST}  {ORANGE}│{RST} {pad_visible(f'{DIM}lottery tickets{RST}', 24)} {ORANGE}│{RST}")
    print(f"  {ORANGE}└{'─'*26}┘  └{'─'*26}┘{RST}")
    # Row 2: Session Time + Power Cost
    print(f"  {ORANGE}┌{'─'*26}┐  ┌{'─'*26}┐{RST}")
    print(f"  {ORANGE}│{RST} {pad_visible(f'{GRAY}SESSION TIME{RST}', 24)} {ORANGE}│{RST}  {ORANGE}│{RST} {pad_visible(f'{GRAY}POWER COST{RST}', 24)} {ORANGE}│{RST}")
    print(f"  {ORANGE}│{RST} {pad_visible(f'{WHT}{time_str}{RST}', 24)} {ORANGE}│{RST}  {ORANGE}│{RST} {pad_visible(f'{WHT}{cost_str}{RST}', 24)} {ORANGE}│{RST}")
    print(f"  {ORANGE}│{RST} {pad_visible(f'{DIM}since start{RST}', 24)} {ORANGE}│{RST}  {ORANGE}│{RST} {pad_visible(f'{DIM}est. at $0.12/kWh{RST}', 24)} {ORANGE}│{RST}")
    print(f"  {ORANGE}└{'─'*26}┘  └{'─'*26}┘{RST}")

    # ═══ LIVE HASH STREAM ═══
    recent_hashes = getattr(client, 'recent_hashes', [])
    hash_lines = []
    if recent_hashes:
        for h in recent_hashes[-8:]:
            prefix = h[:10]
            rest = h[10:18]
            ts = datetime.now().strftime("%H:%M:%S")
            hash_lines.append(f"{DIM}[{ts}]{RST} {GRAY}{prefix}{RST}{DIM}{rest}...{RST}")
    else:
        hash_lines.append(f"{DIM}Waiting for hashes...{RST}")
    
    print(draw_full_box("LIVE HASH STREAM (last 8 hashes)", hash_lines))

    # ═══ ODDS + PRIZE (stacked vertically — no alignment issues) ═══
    weekly_odds_val = 1 - (1 - odds) ** (144 * 7)
    monthly_odds_val = 1 - (1 - odds) ** (144 * 30)
    yearly_odds_val = 1 - (1 - odds) ** (144 * 365)
    net_share = (hr / 650_000_000_000_000_000_000) * 100
    
    odds_lines = [
        f"{DIM}Per Block:{RST}   {RED}{format_odds(odds)}{RST}      {DIM}Per Day:{RST}   {RED}{format_odds(daily_odds)}{RST}",
        f"{DIM}Per Week:{RST}    {RED}{format_odds(weekly_odds_val)}{RST}      {DIM}Per Month:{RST}  {RED}{format_odds(monthly_odds_val)}{RST}",
        f"{DIM}Per Year:{RST}    {RED}{format_odds(yearly_odds_val)}{RST}",
    ]
    print(draw_full_box("🎯 YOUR ODDS", odds_lines))
    
    prize_lines = [
        f"{DIM}Block Reward:{RST}  {GOLD}{BITCOIN_REWARD} ₿{RST}     {DIM}USD Value:{RST}  {WHT}${btc_value:,.0f}{RST}",
        f"{DIM}Network HR:{RST}    {GRAY}~650 EH/s{RST}       {DIM}Your Share:{RST}  {GRAY}{net_share:.2e}%{RST}",
        f"{DIM}Pool:{RST}          {GRN}{client.host}{RST}",
    ]
    print(draw_full_box("🏆 THE PRIZE", prize_lines))

    # ═══ ELECTRICITY ═══
    monthly_cost = daily_cost * 30
    yearly_cost = daily_cost * 365
    ev = yearly_odds_val * btc_value
    elec_lines = [
        f"{DIM}Daily: ~${daily_cost:.2f}  │  Monthly: ~${monthly_cost:.2f}  │  Yearly: ~${yearly_cost:.0f}{RST}",
        f"{DIM}Expected yearly value: ${ev:.6f}  (spoiler: it costs more than you'll win){RST}"
    ]
    print(draw_full_box("💰 ELECTRICITY", elec_lines))

    # ═══ FOOTER ═══
    print(f"""
  {DIM}Each hash = a lottery ticket. 99.99999% nothing. But 3.125 ₿ if we hit.{RST}
  {DIM}Ctrl+C to stop.{RST}""")


# ── CLI entry ─────────────────────────────────────────────────
def print_banner():
    print(f"""
{ORANGE}╔══════════════════════════════════════════════════════════╗
║{RST}              {BLD}🎰  LONGSHOT  v1.0{RST}                          {ORANGE}║
║{RST}        {DIM}Bitcoin Solo Lottery Miner{RST}                        {ORANGE}║
║{RST}      {DIM}Zero deps. Pure Python stdlib.{RST}                      {ORANGE}║
╚══════════════════════════════════════════════════════════╝{RST}
""")

def print_help():
    print(f"""
{WHT}USAGE{RST}
  longshot {DIM}<command>{RST}

{WHT}COMMANDS{RST}
  {ORANGE}mine{RST}          Start solo mining
  {ORANGE}odds{RST}          Calculate odds based on your hashrate
  {ORANGE}simulate{RST}      Run a benchmark + odds report (no mining)

{WHT}EXAMPLES{RST}
  {DIM}# Start mining on ckpool solo:{RST}
  longshot mine

  {DIM}# Calculate your odds:{RST}
  longshot odds
""")


def run_miner(pool_host=None, pool_port=None):
    """Main mining function."""
    if not pool_host:
        # Try pools in order
        for host, port in SOLO_POOLS:
            print(f"  {DIM}Connecting to {host}:{port}...{RST}")
            client = StratumClient(host, port)
            if not client.connect():
                print(f"  {RED}Connection failed.{RST}")
                continue
            
            print(f"  {GRN}Connected!{RST}")
            print(f"  {DIM}Subscribing...{RST}")
            if not client.subscribe():
                print(f"  {RED}Subscribe failed.{RST}")
                client.stop()
                continue
            
            print(f"  {DIM}Authorizing...{RST}")
            if client.authorize():
                pool_host, pool_port = host, port
                break
            else:
                print(f"  {YELLOW}Auth failed on {host} — trying next pool...{RST}")
                client.stop()
        else:
            print(f"  {RED}Could not connect to any pool. Check internet.{RST}")
            return
    else:
        client = StratumClient(pool_host, pool_port)
        if not client.connect():
            print(f"  {RED}Could not connect to {pool_host}:{pool_port}{RST}")
            return
        if not client.subscribe():
            print(f"  {RED}Subscribe failed.{RST}")
            return
        if not client.authorize():
            print(f"  {RED}Auth failed.{RST}")
            return

    print(f"  {GRN}Mining started!{RST}")
    print(f"  {DIM}Each dot = ~500K hashes. Press Ctrl+C to stop.{RST}")
    print(f"  {GRN}☕ Caffeinated — your Mac won't sleep while mining.{RST}\n")

    # Start caffeinate to prevent sleep
    caff = None
    try:
        caff = subprocess.Popen(['caffeinate', '-dimsu'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        pass

    # Start mining in a thread
    result_holder = [None]
    def mine_thread():
        result_holder[0] = client.mine_loop()

    miner_thread = threading.Thread(target=mine_thread, daemon=True)
    miner_thread.start()

    # Dashboard loop
    dashboard_interval = 3  # seconds
    last_dashboard = 0
    dot_count = 0

    try:
        while miner_thread.is_alive():
            time.sleep(0.5)
            elapsed = time.time() - client.start_time if client.start_time else 0

            # Print dots for activity
            if client.submitted - dot_count >= 1_000_000:
                dot_count = client.submitted
                sys.stdout.write(f"{ORANGE}.{RST}")
                sys.stdout.flush()

            # Dashboard update
            if time.time() - last_dashboard >= dashboard_interval:
                print_dashboard(client, elapsed)
                last_dashboard = time.time()

            # Check for result
            if result_holder[0]:
                break

    except KeyboardInterrupt:
        print(f"\n\n  {GOLD}⏹ Mining stopped.{RST}")
    finally:
        client.stop()
        if caff:
            caff.terminate()
            try: caff.wait(timeout=2)
            except: caff.kill()

    # Show result
    result = result_holder[0]
    if result and result.get("type") == "block":
        print(f"\n  {GOLD}🎉🎉🎉  BLOCK FOUND!  🎉🎉🎉{RST}")
        print(f"  {GOLD}Hash: {result['hash']}{RST}")
        print(f"  {GOLD}You just mined 3.125 BTC!{RST}")
        print(f"  {GOLD}Check your pool dashboard to claim.{RST}")
    else:
        elapsed = time.time() - (client.start_time or time.time())
        print(f"\n  {DIM}Session: {timedelta(seconds=int(elapsed))}")
        print(f"  {DIM}Hashes: {client.submitted:,}")
        print(f"  {DIM}Result: No block found (this is normal){RST}")


def cmd_odds():
    """Benchmark and show odds."""
    print(f"\n  {ORANGE}⚡ Benchmarking hashrate...{RST}")
    print(f"  {DIM}Running SHA-256 in a tight loop for 3 seconds...{RST}")

    # Benchmark
    data = b"LongShot Bitcoin Lottery Miner Benchmark Test Vector"
    start = time.time()
    count = 0
    while time.time() - start < 3:
        hashlib.sha256(hashlib.sha256(data + count.to_bytes(8, 'big')).digest()).digest()
        count += 1

    elapsed = time.time() - start
    hr = count / elapsed if elapsed > 0 else 0
    odds_block = odds_per_block(hr)
    daily_odds = 1 - (1 - odds_block) ** 144
    weekly_odds = 1 - (1 - odds_block) ** (144 * 7)
    monthly_odds = 1 - (1 - odds_block) ** (144 * 30)
    yearly_odds = 1 - (1 - odds_block) ** (144 * 365)

    print(f"\n  {BLD}Your Hashrate:{RST}  {ORANGE}{format_hashrate(hr)}{RST}")
    print(f"\n  {BLD}🎯 Odds of Finding a Block{RST}")
    print(f"  {GRAY}├─ Per block:{RST}     {RED}{format_odds(odds_block)}{RST}")
    print(f"  {GRAY}├─ Per day:{RST}       {RED}{format_odds(daily_odds)}{RST}")
    print(f"  {GRAY}├─ Per week:{RST}      {RED}{format_odds(weekly_odds)}{RST}")
    print(f"  {GRAY}├─ Per month:{RST}     {RED}{format_odds(monthly_odds)}{RST}")
    print(f"  {GRAY}└─ Per year:{RST}      {RED}{format_odds(yearly_odds)}{RST}")

    # Cost analysis
    daily_cost = 0.035 * 24 * 0.12  # 35W * 24h * $0.12/kWh
    monthly_cost = daily_cost * 30
    yearly_cost = daily_cost * 365
    btc_value = BITCOIN_REWARD * 100000  # at $100K/BTC

    print(f"\n  {BLD}💰 Cost vs Reward (1 year){RST}")
    print(f"  {GRAY}├─ Electricity:{RST}    ~${yearly_cost:.0f}/year")
    print(f"  {GRAY}├─ Reward if hit:{RST}  ~${btc_value:,.0f}")
    print(f"  {GRAY}└─ Expected value:{RST}  ${yearly_odds * btc_value:,.6f}")
    print(f"\n  {DIM}TL;DR: You're paying ~${yearly_cost:.0f}/year for a {format_odds(yearly_odds)} lotto ticket.{RST}")
    print(f"  {DIM}But if it hits? {BITCOIN_REWARD} ₿. That's the long shot.{RST}")


def main():
    if len(sys.argv) < 2:
        print_banner()
        print_help()
        return

    cmd = sys.argv[1].lower()

    if cmd in ('-h', '--help', 'help'):
        print_banner()
        print_help()
    elif cmd == 'mine':
        print_banner()
        pool_host = sys.argv[2] if len(sys.argv) > 2 else None
        pool_port = int(sys.argv[3]) if len(sys.argv) > 3 else None
        run_miner(pool_host, pool_port)
    elif cmd == 'odds':
        print_banner()
        cmd_odds()
    elif cmd == 'version':
        print(f"LongShot v1.0.0 — Bitcoin Solo Lottery Miner")
    else:
        print(f"{RED}Unknown command: {cmd}{RST}")
        print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
