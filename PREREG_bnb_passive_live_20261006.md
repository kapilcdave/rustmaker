# PREREG — prospective direct-book BNB passive strict-through shadow

Frozen Tuesday, 2026-10-06 at 07:26:01 PDT. Only decision rows with
`wall_ns >= 1791296761000000000` are admissible.

Use the already-running BNB direct-book spot-gap journal without changing its
signal. On the first 10-cent BNB signal in a market:

- wait two seconds after the recorded direct-orderbook response completed;
- join one contract at the displayed bid of the signaled side;
- leave it until close;
- credit a fill only if a later non-block public aggressor print goes strictly
  through the limit;
- hold a credited fill to settlement and charge zero maker fee.

YES uses the recorded YES bid and requires a later NO-taker print below it. NO
uses the recorded NO bid and requires a later YES-taker print above the
corresponding YES ask. At-price prints receive no queue credit.

Report all admissible signals even if unsettled, strict-through fills, P&L per
signal and fill, displayed size and an extra one-cent-per-fill stress. This
short single-session panel has no promotion gate and cannot authorize orders.
