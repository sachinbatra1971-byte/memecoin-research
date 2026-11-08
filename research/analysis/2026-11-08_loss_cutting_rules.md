# Loss-cutting rules test — 8 Nov 2026 (preview)

Entry: minute 17 after graduation (1-minute candles). Exit: sell half at +100% (recovers ₹1,000), rest at +200%; otherwise sell at 16 h. 2% charges included. ₹1,000 per trade.

Filters: liquidity ≥ $30K, Twitter link, mint/freeze revoked, liquidity locked ≥ 95%, top-10 holders ≤ 30%, no serious RugCheck warning, holders ≥ 300, market cap $30K–$2M at entry. (Holders/top-10 from current RugCheck data.)

## Filtered coins (25 trades)
| Rule | Profitable | Avg back per ₹1,000 | Total profit |
|---|---|---|---|
| Base: stop −25% | 60% | ₹1,600 | ₹15,008 |
| Stop −20% | 52% | ₹1,498 | ₹12,449 |
| Stop −15% | 44% | ₹1,444 | ₹11,096 |
| Break-even stop after +50% | 44% | ₹1,492 | ₹12,307 |
| Time stop (sell if not +20% in 25 min) | 60% | ₹1,562 | ₹14,048 |
| No-chase (skip if +40% in prior 5 min) | 63% (19 trades) | ₹1,663 | ₹12,597 |
| Break-even + time stop | 44% | ₹1,454 | ₹11,347 |

## All coins, no filters (306 trades)
| Rule | Profitable | Avg back per ₹1,000 | Total profit |
|---|---|---|---|
| Base: stop −25% | 51% | ₹1,406 | ₹124,157 |
| Stop −20% | 48% | ₹1,387 | ₹118,398 |
| Stop −15% | 44% | ₹1,363 | ₹110,971 |
| Break-even after +50% | 44% | ₹1,344 | ₹105,190 |
| Time stop | 47% | ₹1,346 | ₹105,937 |
| No-chase (280 trades) | 53% | ₹1,426 | ₹119,372 |
| Break-even + time stop | 42% | ₹1,296 | ₹90,645 |

## Finding
Tighter stops, break-even stops and time stops all cut more winners than they saved on losers. The plain −25% stop gave the most total profit in both groups. No-chase slightly raised the win rate and the average per trade but skipped some winners. Next lever: better filters (makers, bundlers) rather than tighter exits.
