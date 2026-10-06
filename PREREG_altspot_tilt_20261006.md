# PREREG — thin-altcoin spot-gap taker/tilt, true out-of-sample (frozen 2026-10-06 ~09:40Z, BEFORE fetching any data after 2026-09-14)

**Origin:** `idea_lab/idea1_open_drift.py`, `idea1b_series_split.py` (tape 2026-07-02 → 2026-09-14).
Fair = Phi(ln(S_k/K')/(sigma_min*sqrt(15-k))), S_k = Coinbase close of the minute ending at open+60k,
K' = floor_strike * exp(-mean basis), sigma_min = std of 1-min log returns over the prior 120 min.
In sample the Kalshi mid under-reacts to spot-minus-strike on thin altcoins (OLS slope of y-mid on fair-mid:
ZEC 0.50, HYPE 0.39, NEAR 0.38, DOGE 0.20; majors BTC 0.05 / ETH -0.05 / XRP -0.10). Series were chosen after seeing the
full-sample slope, so the in-sample half-split is NOT evidence; only the window below is.

**Window:** markets opening 2026-09-14 03:45Z → 2026-10-05 (nothing in it has been looked at).

**Rule (one trade per market):** at the end of minute k in {2,3,5,8}, first k that triggers: buy YES at the bar ask if
fair - ask > 0.10, buy NO at (1 - bar bid) if bid - fair > 0.10. Net of the Kalshi taker fee ceil(0.07 p(1-p)) per contract.

**Primary population:** NEAR, ZEC, HYPE pooled. **Controls (must NOT pass):** BTC, ETH, SOL, XRP pooled.
**Secondary (reported, no decision):** BNB, DOGE.

**PASS** iff pooled primary net c/ct has day-clustered lower 95% bound > 0 AND each of NEAR, ZEC, HYPE point estimate > 0
AND n >= 150 trades AND control pooled point estimate <= 0 (else the rule is generic, not an altcoin effect).
**FAIL** otherwise. A pass authorises ONLY a shadow/maker-tilt build; it is a candle-close snapshot, not an executable fill.
Known optimism: bar-close quote and spot-close are one instant; thin books mean 1-ct depth may not exist at the quote.

## Implementation correction, 2026-10-06

The first scoring script relied on the downloader's file boundary and therefore
included a partial 2026-10-06 after the file grew during collection. That run is
void: "through 2026-10-05" means an exclusive end at
`2026-10-06T00:00:00Z`. The scorer now enforces
`1789357500 <= open_ts < 1791244800`; no model parameter or trade threshold
changed. The corrected report is governing.
