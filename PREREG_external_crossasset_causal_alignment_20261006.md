# Amendment: causal bar alignment for the external BTC/ETH regime rule

Frozen after the source-code row was scored and its timestamp leak was found,
but before scoring this alignment. It is therefore exploratory and has no
promotion authority.

At the Kalshi `t0+5m` close, only Coinbase candles whose open timestamps are at
most `t0+4m` have completed. Preserve every source formula, threshold and state
action, but compute direction and intra-window volatility through Coinbase
`t0+4m`, then enter at the Kalshi `t0+5m` executable side ask.

This answers the implementation question "what would the source minute-5 rule
have done without future data?" The already-preregistered `t0+6m` execution of
the literal source signal remains the governing causal audit.

