"""Every ~15 minutes: discover new Solana memecoins and snapshot their DexScreener stats.

Discovery sources (free, no key):
  - DexScreener latest token profiles   (Nikist rule: "Profile ON")
  - DexScreener latest + top boosted tokens
  - GeckoTerminal newest Solana pools and trending Solana pools

For every tracked coin younger than MAX_AGE_H, one row is saved per run with the
same numbers Nikist's DexScreener filter uses (age, liquidity, market cap, volume,
buys/sells, price change for 5m/1h/6h/24h, socials). Dead coins are kept too,
so the research is not fooled by looking only at survivors.
"""
import calendar
import os
import time

from common import DATA, get_json, now_utc, read_csv, write_csv
import safety

SAFETY_PER_RUN = 50       # RugCheck + GeckoTerminal lookups per run (free limits)

MAX_AGE_H = 48            # stop snapshotting after this age
MIN_LIQ_TRACK = 5_000     # ignore dust pools (below Nikist minimum of $30K by a wide margin)
DS = "https://api.dexscreener.com"
GT = "https://api.geckoterminal.com/api/v2"
GT_HDR = {"Accept": "application/json;version=20230203"}

TRACKED = os.path.join(DATA, "tracked.csv")
TRACKED_FIELDS = ["token", "first_seen_utc", "source"]

CANDS = os.path.join(DATA, "candidates.csv")
CAND_FIELDS = ["token", "pair", "symbol", "pair_created_utc", "first_pass_utc", "candles_done"]
LOOSE_LIQ, LOOSE_MCAP = 20_000, 20_000   # looser than Nikist ($30K) so we can test variations

SNAP_FIELDS = [
    "ts_utc", "token", "pair", "dex", "symbol", "name", "pair_created_utc", "age_h",
    "price_usd", "liq_usd", "mcap", "fdv",
    "vol_m5", "vol_h1", "vol_h6", "vol_h24",
    "buys_m5", "sells_m5", "buys_h1", "sells_h1", "buys_h6", "sells_h6", "buys_h24", "sells_h24",
    "chg_m5", "chg_h1", "chg_h6", "chg_h24",
    "boosts_active", "has_profile", "n_socials", "n_websites", "socials",
]


def discover():
    """Return {token_address: source} for Solana tokens seen in the free feeds."""
    found = {}
    for path, src in [("/token-profiles/latest/v1", "ds_profile"),
                      ("/token-boosts/latest/v1", "ds_boost"),
                      ("/token-boosts/top/v1", "ds_boost_top")]:
        items = get_json(DS + path) or []
        if isinstance(items, dict):
            items = [items]
        for it in items:
            if it.get("chainId") == "solana" and it.get("tokenAddress"):
                found.setdefault(it["tokenAddress"], src)
        time.sleep(1)

    for page in range(1, 11):  # newest pools, up to 10 pages x 20
        d = get_json(f"{GT}/networks/solana/new_pools?page={page}&include=base_token", GT_HDR)
        if not d:
            break
        for p in d.get("data", []):
            liq = float(p["attributes"].get("reserve_in_usd") or 0)
            base = p["relationships"]["base_token"]["data"]["id"].split("_", 1)[-1]
            if liq >= MIN_LIQ_TRACK:
                found.setdefault(base, "gt_new")
        time.sleep(2.5)
    for page in range(1, 4):
        d = get_json(f"{GT}/networks/solana/trending_pools?page={page}&duration=1h", GT_HDR)
        if not d:
            break
        for p in d.get("data", []):
            base = p["relationships"]["base_token"]["data"]["id"].split("_", 1)[-1]
            found.setdefault(base, "gt_trending")
        time.sleep(2.5)
    return found


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return ""


def snapshot(tokens, ts):
    """Fetch DexScreener stats for tokens (30 per call). Returns (rows, too_old set)."""
    rows, too_old, alive = [], set(), set()
    now_ms = ts.timestamp() * 1000
    for i in range(0, len(tokens), 30):
        batch = tokens[i:i + 30]
        pairs = get_json(f"{DS}/tokens/v1/solana/{','.join(batch)}") or []
        best = {}  # keep the most liquid pair per token
        for p in pairs:
            t = (p.get("baseToken") or {}).get("address")
            if t not in batch:
                continue
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if t not in best or liq > ((best[t].get("liquidity") or {}).get("usd") or 0):
                best[t] = p
        for t, p in best.items():
            alive.add(t)
            created = p.get("pairCreatedAt") or 0
            age_h = (now_ms - created) / 3_600_000 if created else ""
            if age_h != "" and age_h > MAX_AGE_H:
                too_old.add(t)
                continue
            tx, vol, chg = p.get("txns") or {}, p.get("volume") or {}, p.get("priceChange") or {}
            info = p.get("info") or {}
            socials = info.get("socials") or []
            rows.append({
                "ts_utc": ts.strftime("%Y-%m-%d %H:%M"),
                "token": t, "pair": p.get("pairAddress"), "dex": p.get("dexId"),
                "symbol": (p.get("baseToken") or {}).get("symbol"),
                "name": (p.get("baseToken") or {}).get("name"),
                "pair_created_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(created / 1000)) if created else "",
                "age_h": round(age_h, 2) if age_h != "" else "",
                "price_usd": p.get("priceUsd"),
                "liq_usd": (p.get("liquidity") or {}).get("usd"),
                "mcap": p.get("marketCap"), "fdv": p.get("fdv"),
                **{f"vol_{k}": vol.get(k) for k in ("m5", "h1", "h6", "h24")},
                **{f"{side}_{k}": (tx.get(k) or {}).get(side) for k in ("m5", "h1", "h6", "h24")
                   for side in ("buys", "sells")},
                **{f"chg_{k}": chg.get(k) for k in ("m5", "h1", "h6", "h24")},
                "boosts_active": (p.get("boosts") or {}).get("active", 0),
                "has_profile": int(bool(info)),
                "n_socials": len(socials), "n_websites": len(info.get("websites") or []),
                "socials": "|".join(s.get("type", "") for s in socials),
            })
        time.sleep(1.2)
    return rows, too_old, alive


def main():
    ts = now_utc()
    tracked = {r["token"]: r for r in read_csv(TRACKED)}
    new = discover()
    added = 0
    for t, src in new.items():
        if t not in tracked:
            tracked[t] = {"token": t, "first_seen_utc": ts.strftime("%Y-%m-%d %H:%M"), "source": src}
            added += 1

    # Only snapshot tokens first seen within the age window (cheap pre-filter).
    cutoff = ts.timestamp() - (MAX_AGE_H + 24) * 3600
    active = []
    for t, r in tracked.items():
        if r.get("done") == "1":
            continue
        try:
            seen = calendar.timegm(time.strptime(r["first_seen_utc"], "%Y-%m-%d %H:%M"))
        except Exception:
            seen = ts.timestamp()
        if seen >= cutoff:
            active.append(t)

    rows, too_old, alive = snapshot(active, ts)
    for t in too_old:
        tracked[t]["done"] = "1"

    cands = {r["pair"]: r for r in read_csv(CANDS)}
    newc = []
    for r in rows:
        if (fnum(r["liq_usd"]) or 0) >= LOOSE_LIQ and (fnum(r["mcap"]) or 0) >= LOOSE_MCAP \
                and r["pair"] and r["pair"] not in cands and r["pair_created_utc"]:
            cands[r["pair"]] = {"token": r["token"], "pair": r["pair"], "symbol": r["symbol"],
                                "pair_created_utc": r["pair_created_utc"],
                                "first_pass_utc": r["ts_utc"], "candles_done": "0"}
            newc.append(r["token"])
    write_csv(CANDS, sorted(cands.values(), key=lambda r: r["pair_created_utc"]), CAND_FIELDS)

    # Point-in-time safety data: coins that just qualified first, then new coins with real liquidity.
    todo, seen_t = [], set()
    for t in newc:
        if t in tracked and tracked[t].get("safety") != "1" and t not in seen_t:
            todo.append((t, "qualified")); seen_t.add(t)
    for r in rows:
        t = r["token"]
        if t in tracked and tracked[t].get("safety") != "1" and t not in seen_t and (fnum(r["liq_usd"]) or 0) >= MIN_LIQ_TRACK:
            todo.append((t, "first_seen")); seen_t.add(t)
    todo = todo[:SAFETY_PER_RUN]
    srows = safety.check(todo, ts) if todo else []
    for sr in srows:
        if sr.get("rc_score") is not None or sr.get("buyers_h1") is not None:
            tracked[sr["token"]]["safety"] = "1"
    if srows:
        write_csv(os.path.join(DATA, "safety", ts.strftime("%Y-%m-%d"), ts.strftime("%H%M") + ".csv.gz"),
                  srows, safety.FIELDS, gz=True)

    if rows:
        out = os.path.join(DATA, "snapshots", ts.strftime("%Y-%m-%d"), ts.strftime("%H%M") + ".csv.gz")
        write_csv(out, rows, SNAP_FIELDS, gz=True)
    write_csv(TRACKED, sorted(tracked.values(), key=lambda r: r["first_seen_utc"]),
              TRACKED_FIELDS + ["done", "safety"])
    print(f"{ts:%Y-%m-%d %H:%M} UTC  discovered={len(new)} new={added} "
          f"active={len(active)} snapshotted={len(rows)} retired={len(too_old)} total_tracked={len(tracked)} candidates={len(cands)} safety_checked={len(srows)}")


if __name__ == "__main__":
    main()
