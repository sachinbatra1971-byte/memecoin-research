# Entry timing test — 7 Nov 2026 (preliminary)

Data: 136 coins with saved minute prices (first 26 h of each coin's main trading pool, i.e. from graduation for Pump.fun coins), grouped into 5-minute candles. No safety filters applied yet.

Exit rules tested on every entry (₹1,000 per trade, 2% fees deducted):
- **Simple:** +60% target vs −25% stop, whichever comes first; otherwise sell at 16 h.
- **Sell-initial:** stop −25% until +60%; at +60% sell enough to recover ₹1,000; the rest exits on a 30% fall from its highest price, or at 16 h.

L-legs (one interpretation of Nikist's rule, on 5-min candles): after the launch move, a pullback of 15%+ from the high; a new leg starts when a 5-min candle closes above the previous candle's high and 10%+ above the pullback low. Entry at that candle's close. Counting stops if price falls below the L1 starting low.

| Entry | Trades | +60% hit first | Avg return per ₹1,000 (sell-initial) | Typical (median) | Losing trades | Doubled or more |
|---|---|---|---|---|---|---|
| Graduation +5 min | 136 | 57% | ₹3,430 | ₹1,486 | 37% | 22% |
| Graduation +10 min | 132 | 55% | ₹3,290 | ₹1,446 | 36% | 22% |
| Graduation +15 min | 127 | 50% | ₹3,149 | ₹1,393 | 39% | 21% |
| Graduation +20 min | 126 | 44% | ₹2,766 | ₹1,149 | 45% | 17% |
| Graduation +30 min | 121 | 36% | ₹2,414 | ₹965 | 52% | 13% |
| Graduation +60 min | 111 | 34% | ₹2,478 | ₹964 | 55% | 14% |
| L1 | 71 | 37% | ₹1,635 | ₹735 | 62% | 10% |
| L2 | 50 | 42% | ₹1,078 | ₹735 | 58% | 2% |
| L3 | 40 | 33% | ₹1,048 | ₹735 | 68% | 3% |
| L4 | 33 | 36% | ₹1,113 | ₹735 | 58% | 6% |
| L5 | 27 | 30% | ₹1,031 | ₹735 | 67% | 4% |
| L6+ | 207 | 28% | ₹986 | ₹735 | 68% | 0% |

Averages are pulled up by a few very large winners (capped at 20x); the median is the "typical" trade.

## Caveats
- Coins in this set were found through DexScreener profiles/boosts and GeckoTerminal new/trending lists and later reached $20K liquidity and market cap — a better-than-average group. Real results will be lower.
- No safety filters yet; fills assumed at the candle close; slippage on early entries may be larger than the 2% allowed.
- Time-of-day samples are still too small to rely on.

## Next
- Collector now records safety data (RugCheck) and unique buyers/sellers (GeckoTerminal) when a coin first appears, so the 13 Nov report can apply the safety filter at the moment of entry.
