# Post-hoc BTC/ETH consensus gate on the thin-altcoin spot-gap rule

Status: **EXPLORATORY ONLY — NO PROMOTION AUTHORITY**

After the NEAR/ZEC/HYPE rule and its OOS result were known, I tested whether
requiring both BTC and ETH to have moved in the intended trade direction would
improve it. This gate was not preregistered and therefore cannot rescue or
promote the parent strategy.

Using the same 1,280 historical OOS decisions as the frozen rule:

- parent rule: 1,280 trades, +2.345c/contract, day-clustered lower bound
  +0.334c;
- BTC+ETH consensus subset: 263 trades, **-0.003c/contract**;
- clustered standard error 2.909c and lower bound **-5.703c**;
- halves **-0.305c / +0.297c**;
- NEAR -2.064c/86, ZEC +0.141c/74, HYPE +1.616c/103.

The intuitive market-wide confirmation filter discarded most of the positive
sample and left no measurable edge. It is closed as a post-hoc idea and must
not be retuned on this period.
