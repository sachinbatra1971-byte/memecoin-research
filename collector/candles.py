"""Every ~2 hours: once a candidate coin is 26+ hours old, save its full
minute-by-minute price history (first 26 hours) from GeckoTerminal.

These candles are what the backtest uses to replay Nikist's rules
(L3/L5 entry, +60% target / -25% stop-loss, sell-initial) and to test better ones.
"""
import calendar
import os
import time

from common import DATA, get_json, now_utc, read_csv, write_csv

GT = "https://api.geckoterminal.com/api/v2"
GT_HDR = {"Accept": "application/json;version=20230203"}
CANDS = os.path.join(DATA, "candidates.csv")
CAND_FIELDS = ["token", "pair", "symbol", "pair_created_utc", "first_pass_utc", "candles_done"]
WINDOW_MIN = 26 * 60
MAX_PER_RUN = 120   # ~2.5s per call keeps us under the free rate limit


def fetch(pair, before_ts, limit):
    url = (f"{GT}/networks/solana/pools/{pair}/ohlcv/minute?aggregate=1&limit={limit}"
           f"&before_timestamp={before_ts}&currency=usd&token=base")
    d = get_json(url, GT_HDR)
    time.sleep(2.5)
    if not d:
        return None
    return ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []


def main():
    now = now_utc().timestamp()
    cands = read_csv(CANDS)
    done_n, tried = 0, 0
    for c in cands:
        if c.get("candles_done") in ("1", "x") or tried >= MAX_PER_RUN:
            continue
        created = calendar.timegm(time.strptime(c["pair_created_utc"], "%Y-%m-%d %H:%M"))
        if now < created + (WINDOW_MIN + 15) * 60:
            continue  # not old enough yet
        tried += 1
        end = created + WINDOW_MIN * 60
        part1 = fetch(c["pair"], end, 1000)              # last ~1000 minutes of the window
        part2 = fetch(c["pair"], end - 1000 * 60, 1000)  # the earlier minutes
        if part1 is None and part2 is None:
            c["candles_done"] = "x"   # pool unknown to GeckoTerminal; skip for good
            continue
        seen, rows = set(), []
        for o in (part1 or []) + (part2 or []):
            ts = int(o[0])
            if ts in seen or ts < created - 60 or ts > end:
                continue
            seen.add(ts)
            rows.append({"ts": ts, "open": o[1], "high": o[2], "low": o[3], "close": o[4], "vol_usd": o[5]})
        rows.sort(key=lambda r: r["ts"])
        day = c["pair_created_utc"][:10]
        write_csv(os.path.join(DATA, "candles", day, f"{c['pair']}.csv.gz"), rows,
                  ["ts", "open", "high", "low", "close", "vol_usd"], gz=True)
        c["candles_done"] = "1"
        done_n += 1
    write_csv(CANDS, cands, CAND_FIELDS)
    waiting = sum(1 for c in cands if c.get("candles_done") == "0")
    print(f"candles saved={done_n} attempted={tried} still_waiting={waiting}")


if __name__ == "__main__":
    main()
