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

BF = os.path.join(DATA, "backfill")
STATE = os.path.join(BF, "state.json")
GRADS = os.path.join(BF, "grads.csv")
GRAD_FIELDS = ["mint", "grad_ts", "status"]
RES = os.path.join(BF, "results.csv")
RES_FIELDS = (["mint", "symbol", "pair", "grad_ts", "twitter", "sample", "ratio", "bars", "first_bar_s"]
              + [f"{p}{e}" for e in ENS for p in ("a", "b", "mc")]
              + ["rc", "rc_mint", "rc_freeze", "rc_lp", "rc_top10", "rc_danger", "rc_holders"])
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
            print(f"  rpc {method} {getattr(e, 'code', type(e).__name__)}"); time.sleep(3 * (a + 1))
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
            todo = [s["signature"] for s in chunk if not s.get("err") and (s.get("blockTime") or 0) < START_TS]
            with cf.ThreadPoolExecutor(3) as ex:
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


def series(rows):
    m = {int(r[0]) // 60 * 60: r for r in rows}
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


def step_enrich(grads, results):
    now = time.time()
    pend = [g for g in grads.values() if g["status"] == "" and int(g["grad_ts"]) < now - 26 * 3600]
    pend.sort(key=lambda g: -int(g["grad_ts"]))          # newest first, walking back in time
    done = 0
    for i in range(0, len(pend), 30):
        if left() < 60:
            break
        batch = pend[i:i + 30]
        pairs = get_json("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(g["mint"] for g in batch)) or []
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
            p = best.get(g["mint"])
            if not p or not p.get("priceUsd") or not (p.get("marketCap") or p.get("fdv")):
                g["status"] = "nopair"; continue
            socials = (p.get("info") or {}).get("socials") or []
            tw = int(any((s.get("type") or "").lower() == "twitter" for s in socials))
            sample = int(not tw and int(g["grad_ts"]) % 10 == 0)
            if not tw and not sample:
                g["status"] = "notw"; continue
            gts = int(g["grad_ts"])
            d = get_json(GT.format(p["pairAddress"], gts + 60000), GT_HDR, tries=3, pause=4)
            time.sleep(2.2)
            rows = (((d or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
            first, B = series(rows)
            ratio = float(p.get("marketCap") or p.get("fdv")) / float(p["priceUsd"])
            r = {"mint": g["mint"], "symbol": (p.get("baseToken") or {}).get("symbol"), "pair": p["pairAddress"],
                 "grad_ts": gts, "twitter": tw, "sample": sample, "ratio": round(ratio), "bars": len(B),
                 "first_bar_s": (first - gts) if B else ""}
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


def main():
    os.makedirs(BF, exist_ok=True)
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    grads = {r["mint"]: r for r in read_csv(GRADS)}
    results = read_csv(RES)
    added = step_list(state, grads)
    done = step_enrich(grads, results)
    json.dump(state, open(STATE, "w"))
    write_csv(GRADS, sorted(grads.values(), key=lambda r: -int(r["grad_ts"])), GRAD_FIELDS)
    write_csv(RES, results, RES_FIELDS)
    st = {}
    for g in grads.values():
        st[g["status"] or "pending"] = st.get(g["status"] or "pending", 0) + 1
    print(f"backfill: +{added} graduations listed (total {len(grads)}), {done} coins analysed, results {len(results)}, status {st}")


if __name__ == "__main__":
    main()
