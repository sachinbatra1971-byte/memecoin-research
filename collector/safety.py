"""Point-in-time safety + real-buyer data for young coins (free, no key).

Called by collect.py for coins when they first qualify (and new coins with real
liquidity), so the backtest knows what was visible *at that moment*:
  - RugCheck: mint/freeze authority, liquidity locked, top-10 holders (excluding
    pools/lockers), insider networks, dev's balance and other coins, risk list.
  - GeckoTerminal: unique buyers/sellers in the last 5m / 1h / 24h, pool age.
"""
import time

from common import get_json

RC = "https://api.rugcheck.xyz/v1/tokens/{}/report"
GT = "https://api.geckoterminal.com/api/v2/networks/solana/tokens/{}/pools?page=1"
GT_HDR = {"Accept": "application/json;version=20230203"}

FIELDS = [
    "ts_utc", "token", "reason", "token_created_utc", "pool_created_utc", "mins_since_creation",
    "rc_score", "rc_risks", "rc_danger_count", "mint_revoked", "freeze_revoked", "lp_locked_pct",
    "top10_ex_pools_pct", "top1_ex_pools_pct", "insider_holders_in_top20", "insider_networks",
    "insider_network_wallets", "creator_balance", "creator_other_tokens", "total_holders",
    "transfer_fee_pct", "launchpad", "rugged",
    "buyers_m5", "sellers_m5", "buyers_h1", "sellers_h1", "buyers_h24", "sellers_h24",
    "buys_h1", "sells_h1",
]


def _rugcheck(token):
    j = get_json(RC.format(token), tries=2, pause=3)
    if not isinstance(j, dict) or not j.get("mint"):
        return {}
    known = j.get("knownAccounts") or {}
    skip = lambda h: (known.get(h.get("owner")) or known.get(h.get("address")) or {}).get("type") in ("AMM", "LOCKER")
    holders = [h for h in (j.get("topHolders") or []) if not skip(h)]
    pcts = sorted((float(h.get("pct") or 0) for h in holders), reverse=True)
    risks = j.get("risks") or []
    markets = j.get("markets") or []
    nets = j.get("insiderNetworks") or []
    ct = j.get("creatorTokens")
    return {
        "token_created_utc": (j.get("detectedAt") or "")[:16].replace("T", " "),
        "rc_score": j.get("score_normalised", j.get("score")),
        "rc_risks": "|".join(f"{r.get('name')}:{r.get('level')}" for r in risks)[:400],
        "rc_danger_count": sum(1 for r in risks if r.get("level") == "danger"),
        "mint_revoked": int(j.get("mintAuthority") is None),
        "freeze_revoked": int(j.get("freezeAuthority") is None),
        "lp_locked_pct": round(max([float((m.get("lp") or {}).get("lpLockedPct") or 0) for m in markets] or [0]), 1),
        "top10_ex_pools_pct": round(sum(pcts[:10]), 2),
        "top1_ex_pools_pct": round(pcts[0], 2) if pcts else "",
        "insider_holders_in_top20": sum(1 for h in holders if h.get("insider")),
        "insider_networks": len(nets),
        "insider_network_wallets": sum(int(n.get("size") or 0) for n in nets),
        "creator_balance": j.get("creatorBalance"),
        "creator_other_tokens": len(ct) if isinstance(ct, list) else "",
        "total_holders": j.get("totalHolders"),
        "transfer_fee_pct": (j.get("transferFee") or {}).get("pct"),
        "launchpad": (j.get("launchpad") or {}).get("name", "") if isinstance(j.get("launchpad"), dict) else "",
        "rugged": int(bool(j.get("rugged"))),
    }


def _gecko(token):
    d = get_json(GT.format(token), GT_HDR, tries=2, pause=3)
    pools = (d or {}).get("data") or []
    if not pools:
        return {}
    a = pools[0].get("attributes") or {}
    tx = a.get("transactions") or {}
    out = {"pool_created_utc": (a.get("pool_created_at") or "")[:16].replace("T", " ")}
    for k in ("m5", "h1", "h24"):
        out[f"buyers_{k}"] = (tx.get(k) or {}).get("buyers")
        out[f"sellers_{k}"] = (tx.get(k) or {}).get("sellers")
    out["buys_h1"] = (tx.get("h1") or {}).get("buys")
    out["sells_h1"] = (tx.get("h1") or {}).get("sells")
    return out


def check(items, ts):
    """items: list of (token, reason). Returns rows for FIELDS."""
    rows = []
    for token, reason in items:
        row = {"ts_utc": ts.strftime("%Y-%m-%d %H:%M"), "token": token, "reason": reason}
        row.update(_rugcheck(token))
        time.sleep(1.0)
        row.update(_gecko(token))
        time.sleep(2.5)
        created = row.get("token_created_utc")
        if created:
            try:
                c = time.mktime(time.strptime(created, "%Y-%m-%d %H:%M"))
                n = time.mktime(time.strptime(row["ts_utc"], "%Y-%m-%d %H:%M"))
                row["mins_since_creation"] = int((n - c) / 60)
            except ValueError:
                pass
        rows.append(row)
    return rows
