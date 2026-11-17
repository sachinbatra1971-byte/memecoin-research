"""Backfill: older Pump.fun graduations, analysed the same way for every period.

Step 1 (list): walk the Pump.fun migration account backwards on Solana (free public RPC)
         and record every graduation (mint + time).
Step 2 (enrich): for graduations older than 26 h, look up DexScreener (pair, Twitter,
         supply), download 1-minute prices from GeckoTerminal, simulate the fixed trade
         rules at several entry minutes, and check RugCheck (as it looks today).
Only results are saved (no raw candles) to keep the repository small.
Coins without a Twitter link are sampled (1 in 10) as a no-filter baseline.
"""
import concurrent.futures as cf
import json
import os
import time
import urllib.request

from common import DATA, UA, get_json, read_csv, write_csv

RPC = "https://api.mainnet-beta.solana.com"   # archival public RPC (40 req / 10 s per method)
MIGRATION = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
WSOL = "So11111111111111111111111111111111111111112"
START_TS = 1794528000          # 2026-11-13 00:00 UTC: walk backwards from here
STOP_TS = 1779062400           # 2026-05-18 00:00 UTC: ~6 months (GeckoTerminal free limit)
BUDGET_S = 23 * 60
LIST_BUDGET_S = 11 * 60
ENS = [5, 10, 15, 20, 30, 60]
SAMPLE = 4                     # before 7 Nov: look up 1 in 4 graduations (public RPC is rate-limited)
SAMPLE_FROM_TS = 1794009600    # 2026-11-07 00:00 UTC

BF = os.path.join(DATA, "backfill")
STATE = os.path.join(BF, "state.json")
GRADS = os.path.join(BF, "grads.csv")
GRAD_FIELDS = ["mint", "grad_ts", "status"]
RES = os.path.join(BF, "results.csv")
RES_FIELDS = (["mint", "symbol", "pair", "grad_ts", "twitter", "sample", "ratio", "bars", "first_bar_s"]
              + [f"{p}{e}" for e in ENS for p in ("a", "b", "mc")]
              + ["rc", "rc_mint", "rc_freeze", "rc_lp", "rc_top10", "rc_danger", "rc_holders", "src"])
GTB = "https://api.geckoterminal.com/api/v2/networks/solana"
GT = "https://api.geckoterminal.com/api/v2/networks/solana/pools/{}/ohlcv/minute?aggregate=1&limit=1000&currency=usd&before_timestamp={}"
GT_HDR = {"Accept": "application/json;version=20230203"}
T0 = time.time()


def left():
    return BUDGET_S - (time.time() - T0)


def rpc(method, params, tries=3):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for a in range(tries):
        try:
            req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read().decode())
            if "error" in j and j["error"].get("code") in (429, -32005):
                time.sleep(3 * (a + 1)); continue
            return j
        except Exception as e:
            print(f"  rpc {method} {getattr(e, 'code', type(e).__name__)}"); time.sleep(8 * (a + 1))
    return {}


def grad_from_tx(sig):
    time.sleep(1.0)                                   # stay under the public RPC limit
    j = rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}])
    t = j.get("result")
    if not t or t.get("meta") is None or t["meta"].get("err"):
        return None
    logs = t["meta"].get("logMessages") or []
    if not any("Instruction: Migrate" in l for l in logs):
        return None
    for b in t["meta"].get("postTokenBalances") or []:
        if b.get("mint") and b["mint"] != WSOL:
            return b["mint"], t.get("blockTime")
    return None


def step_list(state, grads):
    added = 0
    budget = LIST_BUDGET_S
    backlog = sum(1 for g in grads.values() if g["status"] == "")
    if backlog > 1200:                          # let the analysis catch up first
        print(f"  list: skipped, {backlog} coins still waiting for analysis"); return 0
    while time.time() - T0 < budget:
        if state.get("cursor_ts") and state["cursor_ts"] < STOP_TS:
            break
        opt = {"limit": 1000}
        if state.get("cursor"):
            opt["before"] = state["cursor"]
        sigs = (rpc("getSignaturesForAddress", [MIGRATION, opt]).get("result")) or []
        if not sigs:
            print("  list: no signatures returned"); break
        for k in range(0, len(sigs), 100):
            if time.time() - T0 > budget:
                break
            chunk = sigs[k:k + 100]
            todo = [s["signature"] for s in chunk if not s.get("err") and (s.get("blockTime") or 0) < START_TS
                    and ((s.get("blockTime") or 0) >= SAMPLE_FROM_TS or sum(map(ord, s["signature"][:8])) % SAMPLE == 0)]
            with cf.ThreadPoolExecutor(2) as ex:
                for g in ex.map(grad_from_tx, todo):
                    if g:
                        mint, bt = g
                        if mint in grads:
                            grads[mint]["grad_ts"] = str(min(int(grads[mint]["grad_ts"]), bt))
                        else:
                            grads[mint] = {"mint": mint, "grad_ts": str(bt), "status": ""}; added += 1
            state["cursor"] = chunk[-1]["signature"]
            state["cursor_ts"] = chunk[-1].get("blockTime")
        print(f"  list: reached {time.strftime('%Y-%m-%d %H:%M', time.gmtime(state['cursor_ts'] or 0))} UTC, total grads {len(grads)}")
    return added


def series(rows, start=0):
    m = {int(r[0]) // 60 * 60: r for r in rows if int(r[0]) >= start // 60 * 60}
    if not m:
        return 0, []
    ks = sorted(m)
    B, last = [], float(m[ks[0]][1])
    t = ks[0]
    while t <= ks[-1]:
        r = m.get(t)
        if r:
            B.append((float(r[1]), float(r[2]), float(r[3]), float(r[4]))); last = float(r[4])
        else:
            B.append((last, last, last, last))
        t += 60
    return ks[0], B


def trade(B, EN, plan, stop=0.75, H=960):
    p = B[EN][3]
    half, val = False, 0.0
    end = min(len(B) - 1, EN + H)
    for i in range(EN + 1, end + 1):
        o, h, l, c = B[i]
        h = min(h, c * 3)
        if l <= p * stop:
            return (val + (0.5 if half else 1) * stop) * 0.98
        if plan == "A" and h >= 1.6 * p:
            return 1.6 * 0.98
        if plan == "B":
            if not half and h >= 2 * p:
                half, val = True, 1.0
            if half and h >= 3 * p:
                return (val + 1.5) * 0.98
    last = min(B[end][3] / p, 20)
    return (val + (0.5 if half else 1) * last) * 0.98


def rugcheck(mint):
    j = get_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", tries=2, pause=4)
    if not isinstance(j, dict) or not j.get("mint"):
        return {"rc": "0"}
    known = j.get("knownAccounts") or {}
    skip = lambda h: (known.get(h.get("owner")) or known.get(h.get("address")) or {}).get("type") in ("AMM", "LOCKER")
    pcts = sorted((float(h.get("pct") or 0) for h in (j.get("topHolders") or []) if not skip(h)), reverse=True)
    danger = [r.get("name", "") for r in (j.get("risks") or []) if r.get("level") == "danger"
              and "liquidity" not in r.get("name", "").lower() and "lp provider" not in r.get("name", "").lower()]
    return {"rc": "1", "rc_mint": int(j.get("mintAuthority") is None), "rc_freeze": int(j.get("freezeAuthority") is None),
            "rc_lp": round(max([float((m.get("lp") or {}).get("lpLockedPct") or 0) for m in (j.get("markets") or [])] or [0]), 1),
            "rc_top10": round(sum(pcts[:10]), 2), "rc_danger": len(danger), "rc_holders": j.get("totalHolders")}


def gt_twitter(mint):
    """1 if the token's own metadata lists a Twitter/X account, 0 if not, None if the request failed."""
    d = get_json(f"{GTB}/tokens/{mint}/info", GT_HDR, tries=2, pause=5)
    time.sleep(2.2)
    if d is None:
        return None
    a = (d.get("data") or {}).get("attributes") or {}
    return int(bool(a.get("twitter_handle")))


def gt_pool(mint):
    """Main pool + supply from GeckoTerminal, shaped like a DexScreener pair."""
    d = get_json(f"{GTB}/tokens/{mint}?include=top_pools", GT_HDR, tries=2, pause=5)
    time.sleep(2.2)
    data = (d or {}).get("data") or {}
    a = data.get("attributes") or {}
    pools = ((data.get("relationships") or {}).get("top_pools") or {}).get("data") or []
    if not a or not pools:
        return None
    try:
        supply = float(a.get("normalized_total_supply") or float(a["total_supply"]) / 10 ** int(a.get("decimals") or 6))
    except (KeyError, TypeError, ValueError):
        return None
    return {"pairAddress": pools[0]["id"].split("_", 1)[-1], "priceUsd": "1", "marketCap": supply,
            "baseToken": {"symbol": a.get("symbol")}}


def step_enrich(grads, results):
    now = time.time()
    pend = [g for g in grads.values() if g["status"] == "" and int(g["grad_ts"]) < now - 26 * 3600]
    pend.sort(key=lambda g: -int(g["grad_ts"]))          # newest first, walking back in time
    done = fails = 0
    for i in range(0, len(pend), 30):
        if left() < 60:
            break
        batch = pend[i:i + 30]
        pairs = get_json("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(g["mint"] for g in batch))
        if pairs is None:                       # request failed (not just "no pairs")
            print("  enrich: DexScreener request failed - stopping this run"); break
        if not isinstance(pairs, list):
            pairs = []
        best = {}
        for p in pairs if isinstance(pairs, list) else []:
            t = (p.get("baseToken") or {}).get("address")
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if t and (t not in best or liq > ((best[t].get("liquidity") or {}).get("usd") or 0)):
                best[t] = p
        time.sleep(1.2)
        for g in batch:
            if left() < 40:
                break
            tw = gt_twitter(g["mint"])               # same Twitter source for every coin, old or new
            if tw is None:                           # no info (or request failed)
                fails += 1
                if fails >= 5:
                    break                            # GeckoTerminal not answering: try next run
                g["status"] = "noinfo"; continue
            fails = 0
            sample = int(not tw and int(g["grad_ts"]) % 10 == 0)
            if not tw and not sample:
                g["status"] = "notw"; continue
            p, src = best.get(g["mint"]), "ds"
            if not p or not p.get("priceUsd") or not (p.get("marketCap") or p.get("fdv")):
                p, src = gt_pool(g["mint"]), "gt"   # coin no longer listed on DexScreener
                if not p:
                    g["status"] = "nopair"; continue
            gts = int(g["grad_ts"])
            d = get_json(GT.format(p["pairAddress"], gts + 60000), GT_HDR, tries=3, pause=4)
            time.sleep(2.2)
            rows = (((d or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
            first, B = series(rows, gts)
            ratio = float(p.get("marketCap") or p.get("fdv")) / float(p["priceUsd"])
            r = {"mint": g["mint"], "symbol": (p.get("baseToken") or {}).get("symbol"), "pair": p["pairAddress"],
                 "grad_ts": gts, "twitter": tw, "sample": sample, "ratio": round(ratio), "bars": len(B),
                 "first_bar_s": (first - gts) if B else "", "src": src}
            for e in ENS:
                if len(B) > e + 1:
                    r[f"a{e}"] = round(trade(B, e, "A"), 4); r[f"b{e}"] = round(trade(B, e, "B"), 4)
                    r[f"mc{e}"] = round(B[e][3] * ratio)
            mc15 = r.get("mc15") or 0
            if tw and 30000 <= mc15 <= 2e6:
                r.update(rugcheck(g["mint"])); time.sleep(1.5)
            results.append(r)
            g["status"] = "done" if B else "nodata"
            done += 1
    return done


SHARDS = 4                      # analysis is split over parallel GitHub jobs (each gets its own rate limit)


def shard_of(mint):
    return sum(map(ord, mint)) % SHARDS


def load_status():
    st = {}
    for k in range(SHARDS):
        for r in read_csv(os.path.join(BF, f"status_{k}.csv")):
            st[r["mint"]] = r["status"]
    return st


def main():
    mode = os.environ.get("BF_MODE", "all")      # list | enrich | more | all
    os.makedirs(BF, exist_ok=True)
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    grads = {r["mint"]: r for r in read_csv(GRADS)}
    orig = {m: g["status"] for m, g in grads.items()}
    for m, s in load_status().items():
        if m in grads and not grads[m]["status"]:
            grads[m]["status"] = s

    if mode == "more":                           # is there still work? (used to chain runs)
        pending = sum(1 for g in grads.values() if g["status"] == "")
        listing_left = not (state.get("cursor_ts") and state["cursor_ts"] < STOP_TS)
        print("yes" if pending or listing_left else "no")
        return

    if mode == "enrich":
        k = int(os.environ["BF_SHARD"])
        mine = {m: g for m, g in grads.items() if shard_of(m) == k}
        rpath = os.path.join(BF, f"results_{k}.csv")
        results = read_csv(rpath)
        done = step_enrich(mine, results)
        rows = [{"mint": m, "status": g["status"]} for m, g in mine.items() if g["status"] and not orig[m]]
        write_csv(os.path.join(BF, f"status_{k}.csv"), rows, ["mint", "status"])
        write_csv(rpath, results, RES_FIELDS)
        left_n = sum(1 for g in mine.values() if g["status"] == "")
        print(f"backfill shard {k}: {done} coins analysed this run, {len(results)} results in shard, {left_n} still waiting")
        return

    results = read_csv(RES)
    added = step_list(state, grads)
    done = step_enrich(grads, results) if mode == "all" else 0
    json.dump(state, open(STATE, "w"))
    write_csv(GRADS, sorted(grads.values(), key=lambda r: -int(r["grad_ts"])), GRAD_FIELDS)
    if mode == "all":
        write_csv(RES, results, RES_FIELDS)
    st = {}
    for g in grads.values():
        st[g["status"] or "pending"] = st.get(g["status"] or "pending", 0) + 1
    print(f"backfill ({mode}): +{added} graduations listed (total {len(grads)}), {done} analysed, status {st}")


if __name__ == "__main__":
    main()
