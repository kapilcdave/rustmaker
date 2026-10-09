//! `fairvalue`: an **independent** model price for the 15M return-strike digital, and the quote
//! pair it implies. No term in here reads the Kalshi book.
//!
//! Every pricing path this engine had before this module anchored on the Kalshi mid and nudged it
//! (`residual::Spot::fair` is `logit(mid) + 1.6·ẑ`). That makes the quote a function of the thing
//! we are trying to price, and it is why a two-sided seat is *structurally* blind to a
//! mispricing: at an even side split the calibration coefficient is exactly zero
//! (`kalshi-scalp/FINDINGS_tail_seller_surface.md`). A model price computed without the book is
//! what un-blinds it, and it is the only thing that can decide to quote **one**-sided.
//!
//! ## The contract
//!
//! From a live payload, checked against all nine series 2026-10-07:
//!
//! ```text
//! ticker       KXETH15M-26OCT071545-45
//! floor_strike 2565.41            <- the OPEN 60s index average; already realised, exact
//! strike_type  greater_or_equal
//! rules        "...average of the sixty seconds of CF Benchmarks' ETHUSDRTI before 3:45 PM EDT
//!               is at least the ... sixty seconds ... before 3:30 PM EDT ..."
//! ```
//!
//! So this is `P(A_T >= K)` with `K` a **known constant** and `A_T` the 60-second average of the
//! settlement index ending at close. Nothing here is estimated except the index's forward
//! distribution, which is two numbers: a level and a variance.
//!
//! ## The level — why the strike cannot be compared with raw spot
//!
//! `K` is in CF index units. Our spot is a median of venue mids, several of them USDT-quoted. A
//! 10 bp basis between the two is ordinary, and 10 bp against a 15-minute `sigma_eff` of ~14 bp
//! moves `z` by ~0.7 — **~25 cents of probability**. A level comparison against the strike is
//! therefore invalid on raw spot, and this is the single largest trap in the design.
//!
//! The fix needs no basis model. The venue publishes the index itself
//! (`cfbenchmarks_value*`), and `FINDINGS_altfeed_index_path_20261007.md` measured its lateness:
//! CF->Kalshi 21-34 ms, Kalshi queue 3 ms, Kalshi->us 1.6 ms, **CF->us 26-73 ms**. So take the
//! **level** from the index (exact, in the strike's units, tens of ms stale) and the **increment
//! since that stamp** from fast spot (fresh, and read as a per-venue *return* then medianed, so
//! the basis cancels by construction — see `fastspot::Consolidated::venue_rets_bps`):
//!
//! ```text
//! level = index_value * (1 + median_venue_return(from index.source_ts, to now))
//! ```
//!
//! Only our 1.6 ms leg of that 38 ms chain is ours to lose, which is the measured sense in which
//! local speed is finished as a lever: the decision path is 45 µs
//! (`FINDINGS_signing_and_transport_20261006.md`) and the rest belongs to CF and to Kalshi.
//!
//! ## The variance — the terminal average is not the terminal value
//!
//! Settlement is the mean of the final `w = 60 s`, not the close. With `ln I` a driftless
//! Brownian of per-second vol `sigma` and `Y = (1/w)integral of W over [tau-w, tau]`,
//! `Var(Y) = tau - 2w/3` exactly. Averaging removes `2w/3 = 40 s` of variance, which at
//! `tau = 120 s` is a third of it — not a refinement, a first-order term. Below `tau = w` part of
//! the settlement window is already realised and this form does not apply, so the pricer refuses
//! to quote there (the engine's `--stop-before-close-s` default of 120 s is already outside it).
//!
//! **No drift term, deliberately.** A momentum tilt on this panel is a closed branch
//! (`kalshi-15m-crypto-mid-continues-and-the-sweep-has-no-side`), and the fitted `1.6` in
//! `residual::Spot::fair` is exactly the coefficient that closure applies to. The model prices
//! the contract; it does not forecast the asset.
//!
//! ## Why there is no width gate
//!
//! Avellaneda-Stoikov's prescription is its spread term `(2/gamma)ln(1+gamma/k)`, and on this
//! venue `k` is **not identified**: the fill hazard ratio inverts past ~8 s of resting, `k` goes
//! negative in 3 of 8 age bins, the implied optimum spans 1.65c to undefined and brackets what
//! the venue already quotes, and order age is set by our own requote cadence
//! (`as-arrival-decay-k-is-not-identified`). GLFT cannot repair it: a maker journal holds only
//! the distances it quoted, and ours holds two. So this module uses the half of A-S that **is**
//! identified — the inventory skew on the reservation price, which has no `k` in it — and takes
//! the margin as a declared cost, not as a fitted optimum.
//!
//! The gate that replaces width falls out of pricing and needs no parameter: bid at
//! `r - margin` **iff that is at or above the others' bid**, because bidding below it cannot
//! fill and joining above it is negative by our own model. Agreement with the mid gives a
//! two-sided quote at the touch; disagreement gives a one-sided one. Width never enters, which
//! matches the measurement: real-print maker gross is flat at +0.249/+0.264/+0.252/+0.240/+0.244
//! c/ct from a 1c to a 10c book (`real-print-maker-ledger-15m-venue-normal`), so a width gate on
//! this panel buys nothing it costs.

use crate::book::PRICE_SCALE;

/// The settlement average's length, seconds. `rules_primary` says sixty on all nine series.
pub const SETTLE_AVG_S: f64 = 60.0;

/// The settlement index a 15M series resolves on, in the WebSocket's `index_ids` vocabulary.
/// Read verbatim out of `rules_primary` ("the simple average of the sixty seconds of CF
/// Benchmarks' ETHUSDRTI before ..."), checked 2026-10-07 against the live markets of all nine
/// series. BTC's index is named `BRTI`; every other asset's is `{ASSET}USD_RTI`.
pub fn index_id_of_series(series: &str) -> Option<String> {
    crate::fastspot::asset_of_series(series)
        .map(|a| if a == "BTC" { "BRTI".to_owned() } else { format!("{a}USD_RTI") })
}

/// Standard normal CDF, Abramowitz & Stegun 26.2.17 (|error| < 7.5e-8). The lattice here is
/// 0.1c = 1e-3 in probability, so this is four orders finer than anything it feeds.
pub fn norm_cdf(x: f64) -> f64 {
    const B: [f64; 5] = [0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429];
    let a = x.abs();
    // The polynomial underflows to the asymptote long before f64 does; short-circuit so a
    // saturated z returns an exact 0 or 1 rather than a denormal.
    if a > 12.0 {
        return if x > 0.0 { 1.0 } else { 0.0 };
    }
    let t = 1.0 / (1.0 + 0.2316419 * a);
    let pdf = (-0.5 * a * a).exp() / (2.0 * std::f64::consts::PI).sqrt();
    let poly = B.iter().rev().fold(0.0, |acc, b| (acc + b) * t);
    let upper = pdf * poly; // P(Z > a)
    if x >= 0.0 { 1.0 - upper } else { upper }
}

/// `P(A_T >= K)` in **cents**, where `A_T` is the 60-second index average ending at close.
///
/// `level` is the index-anchored level (see the module note — never a raw spot level), `tau_s`
/// the seconds to close, and `sigma` the per-root-second log vol of the index. Returns `None`
/// wherever the model is not entitled to an opinion: a non-positive level or strike, a
/// settlement window that has already begun (`tau_s <= SETTLE_AVG_S`), or a vol that is zero or
/// non-finite. `None` means *do not quote* — never "quote at the mid".
pub fn digital_cents(level: f64, strike: f64, tau_s: f64, sigma: f64) -> Option<f64> {
    if !(level > 0.0 && strike > 0.0 && sigma > 0.0) || !level.is_finite() || !sigma.is_finite() {
        return None;
    }
    // Var of the terminal average, not of the terminal value: tau - 2w/3.
    let var_s = tau_s - 2.0 * SETTLE_AVG_S / 3.0;
    if tau_s <= SETTLE_AVG_S || var_s <= 0.0 {
        return None;
    }
    let sigma_eff = sigma * var_s.sqrt();
    if sigma_eff <= 0.0 {
        return None;
    }
    Some(100.0 * norm_cdf((level / strike).ln() / sigma_eff))
}

/// Cents of fair value per basis point of index move, i.e. the digital's delta in the units a
/// quote lives in. `100·phi(z)/sigma_eff` with `sigma_eff` in bps.
///
/// This is the number that governs the whole seat, and it is large: at 27% annual vol (the
/// measured 15M conditional vol, `kalshi-15m-ladder-prices-conditional-vol`) and `tau = 900 s`,
/// `sigma_eff` is **14.1 bp** and the delta is **2.83 c/bp**. The contract is a coin flip only
/// within ±14 bp of its strike and is a 1c-or-99c contract beyond ±35 bp — and a 15-minute ETH
/// move of 35 bp is ordinary. At `tau = 120 s` (the engine's `--stop-before-close-s`) `sigma_eff`
/// is 4.3 bp and the delta is **9.28 c/bp**, 3.3× steeper.
pub fn delta_c_per_bp(level: f64, strike: f64, tau_s: f64, sigma: f64) -> Option<f64> {
    let var_s = tau_s - 2.0 * SETTLE_AVG_S / 3.0;
    if !(level > 0.0 && strike > 0.0 && sigma > 0.0) || tau_s <= SETTLE_AVG_S || var_s <= 0.0 {
        return None;
    }
    let sigma_eff = sigma * var_s.sqrt();
    let z = (level / strike).ln() / sigma_eff;
    let pdf = (-0.5 * z * z).exp() / (2.0 * std::f64::consts::PI).sqrt();
    Some(100.0 * pdf / (sigma_eff * 10_000.0))
}

/// The margin a quote must carry just to survive its own staleness: how far fair value drifts
/// during `reaction_s`, the gap between the index moving and our amend landing in the book.
///
/// `100·phi(z)·sigma·sqrt(reaction_s)/sigma_eff`. **This is the derived replacement for the width
/// gate.** A width gate asks how wide the book is; this asks how much the price we are quoting
/// can move before we can change it, which is the quantity that actually decides whether a
/// resting quote earns.
///
/// At our measured reaction (print/index -> our change in the book: **11.6 ms** post, 10.6 ms
/// cancel, 4.60 ms amend->book, `kalshi-15m-crypto-competitors-react-twice-as-fast-as-the-ohio-box`)
/// it reads **0.147 c at the money at `tau = 900 s`**, and ~0.48 c at `tau = 120 s`. Against the
/// real-print maker gross of **+0.25 c/ct** (`real-print-maker-ledger-15m-venue-normal`) that is
/// 59% of the available income at the money early, and more than all of it at the money late.
///
/// Note the sign of its gradient: it carries `phi(z)`, so it is **largest at the money** and
/// falls away from it — the opposite shape to a book-width gate, and the reason the viable cells
/// are away from the strike and early rather than wherever the book happens to be wide.
pub fn latency_margin_c(level: f64, strike: f64, tau_s: f64, sigma: f64, reaction_s: f64) -> Option<f64> {
    if !(reaction_s > 0.0) || !reaction_s.is_finite() {
        return None;
    }
    // delta_c_per_bp is per bp of level; the drift over `reaction_s` is `sigma*sqrt(reaction_s)`
    // in log units, which is `1e4*sigma*sqrt(reaction_s)` bps.
    let d = delta_c_per_bp(level, strike, tau_s, sigma)?;
    let drift_bp = 10_000.0 * sigma * reaction_s.sqrt();
    Some(d * drift_bp)
}

/// The margin a quote must carry to cover **level uncertainty**: how wrong the fair value is when
/// the anchored level is wrong by `precision_bp`.
///
/// ⚑ **This is the dominant term, and it is 15-25× the latency toll.** Measured 2026-10-07 by
/// inverting the model on five live 15M books (`FINDINGS_fair_value_precision_wall_20261007.md`):
/// the level the Kalshi mid implies and our own Coinbase spot agree to a mean of **+0.4 bp**,
/// range **[−1.2, +2.0] bp** — i.e. the market and we agree about the underlying almost exactly.
/// But at a delta of **2.3-3.7 c/bp**, that ~1 bp of residual level precision is **2-4 cents** of
/// fair-value precision, against a measured maker gross of **+0.25 c/ct** and a quoted spread of
/// **1c**.
///
/// So the market's own quoted spread is *tighter than the resolution of any level-based model*,
/// and that — not latency, not the width gate — is why a model-priced maker cannot beat this
/// book at the money. The 1c spread is not pricing a model; it is pricing a queue.
///
/// The term carries `phi(z)`, so like the latency toll it collapses in the wings. Setting it
/// against the available gross is what `quotable` does below, and the band it returns is the
/// deci-tick wing — independently rediscovering the one Kalshi crypto cell that has ever made
/// real money here (the 0.97 wing scalp, +$21.83 over 150 settlements).
pub fn level_margin_c(level: f64, strike: f64, tau_s: f64, sigma: f64, precision_bp: f64) -> Option<f64> {
    if !(precision_bp >= 0.0) || !precision_bp.is_finite() {
        return None;
    }
    Some(delta_c_per_bp(level, strike, tau_s, sigma)? * precision_bp)
}

/// Is a model-priced quote entitled to exist here at all?
///
/// `gross_c` is what the seat can earn — the measured maker gross, or the half-spread, whichever
/// the caller is willing to defend. The quote is admissible only where the two derived tolls fit
/// inside it:
///
/// ```text
/// latency_margin_c + level_margin_c <= gross_c
/// ```
///
/// Both tolls carry `phi(z)`, so this is a **band in `|z|`, not in book width** — a derived
/// entry gate with no fitted parameter, replacing a width gate that was measured flat. Returns
/// the total required margin alongside the verdict so a caller can journal why it refused.
pub fn quotable(
    level: f64, strike: f64, tau_s: f64, sigma: f64, reaction_s: f64, precision_bp: f64, gross_c: f64,
) -> Option<(bool, f64)> {
    let lat = latency_margin_c(level, strike, tau_s, sigma, reaction_s)?;
    let lvl = level_margin_c(level, strike, tau_s, sigma, precision_bp)?;
    let need = lat + lvl;
    Some((need <= gross_c, need))
}

/// Per-root-second log vol of the settlement index, as an EWMA of squared log returns sampled on
/// a fixed grid.
///
/// The grid is the point: sampling on every tick makes the estimate a function of the publisher's
/// cadence rather than of the asset, and the nine indices publish at 200 ms to 1 s
/// (`FINDINGS_altfeed_index_path_20261007.md`). Sampling at a declared interval makes the number
/// comparable across assets and across days.
pub struct Vol {
    /// Sampling interval, µs. One second: the shortest grid every index populates.
    step_us: i64,
    /// EWMA half-life in samples.
    lambda: f64,
    last: Option<(i64, f64)>,
    /// EWMA of squared log return **per sample**, so `sigma = sqrt(ewma / step_s)`.
    ewma: Option<f64>,
    samples: u32,
}

impl Vol {
    /// `half_life` is in samples (so in seconds at the default grid). 120 samples ~ 2 minutes,
    /// which is one 15M market's worth of window and the shortest span that is not mostly noise.
    pub fn new(step_us: i64, half_life: f64) -> Self {
        Self {
            step_us: step_us.max(1),
            lambda: 0.5f64.powf(1.0 / half_life.max(1.0)),
            last: None,
            ewma: None,
            samples: 0,
        }
    }

    /// Feed a level observation. Ignores anything that does not advance the grid, so the caller
    /// may push every frame.
    pub fn push(&mut self, now_us: i64, level: f64) {
        if !(level > 0.0) || !level.is_finite() {
            return;
        }
        let Some((t0, p0)) = self.last else {
            self.last = Some((now_us, level));
            return;
        };
        let dt = now_us - t0;
        if dt < self.step_us {
            return;
        }
        // Scale to one grid step: a gap of several steps (a stalled publisher, a reconnect)
        // contributes its own variance once, not once per step it spans.
        let r = (level / p0).ln();
        let per_step = r * r * (self.step_us as f64 / dt as f64);
        self.ewma = Some(match self.ewma {
            Some(e) => self.lambda * e + (1.0 - self.lambda) * per_step,
            None => per_step,
        });
        self.samples = self.samples.saturating_add(1);
        self.last = Some((now_us, level));
    }

    /// Per-root-second vol, or `None` before `min_samples`. Clamped into `[floor, ceil]`, both in
    /// per-root-second units, because an unclamped vol is the one input that can move the fair
    /// value to 0 or 100 and quote a whole ladder at the wings.
    pub fn sigma(&self, min_samples: u32, floor: f64, ceil: f64) -> Option<f64> {
        if self.samples < min_samples {
            return None;
        }
        let per_step = self.ewma?;
        let step_s = self.step_us as f64 / 1e6;
        let s = (per_step / step_s).sqrt();
        s.is_finite().then(|| s.clamp(floor, ceil))
    }

    pub fn samples(&self) -> u32 {
        self.samples
    }
}

/// Annualised vol -> per-root-second, for stating a floor and a ceiling in the unit a human
/// thinks in. 365 days of a 24/7 asset.
pub fn annual_to_per_sqrt_s(annual: f64) -> f64 {
    annual / (365.0 * 86_400.0f64).sqrt()
}

/// The last index value and the venue stamp CF computed it at, which is the clock the spot
/// increment must be measured from — **not** our receipt clock, which is 26-73 ms later.
#[derive(Clone, Copy, Debug)]
pub struct IndexTick {
    pub value: f64,
    /// CF's own stamp, µs. `source_ts_ms` on the 5 Hz channel, `data.time` on the 1 Hz one.
    pub source_us: i64,
    /// Our receipt, µs, for staleness only.
    pub recv_us: i64,
}

/// The index-anchored level: an exact level in the strike's units, carried forward by the spot
/// return since CF stamped it. `ret_bps` is the median per-venue return over
/// `[tick.source_us, now]`, which is what makes this immune to the USDT/perp basis.
pub fn anchored_level(tick: &IndexTick, ret_bps: f64) -> Option<f64> {
    if !(tick.value > 0.0) || !ret_bps.is_finite() {
        return None;
    }
    let level = tick.value * (1.0 + ret_bps / 10_000.0);
    (level > 0.0).then_some(level)
}

/// A quote pair in `PRICE_SCALE` units. `None` on a side is a priced refusal: our own model says
/// the other side of that touch is already through fair.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct Pair {
    pub bid: Option<i64>,
    pub ask: Option<i64>,
}

/// Largest grid price `<= px`, and smallest `>= px`, on the venue's tapered lattice. `ranges` is
/// `(start, end, step)` in `PRICE_SCALE` units as `price_ranges` builds it; the crypto 15M grid
/// is 0.1c below 10c and above 90c and 1c between, so neither bound may be assumed.
fn grid_floor(ranges: &[(i64, i64, i64)], px: i64) -> Option<i64> {
    let &(start, _, step) = ranges.iter().find(|(a, b, _)| *a <= px && px < *b).or_else(|| {
        // Above the last range's start: snap into its own grid rather than off the end.
        ranges.last().filter(|(a, _, _)| px >= *a)
    })?;
    Some(start + (px - start) / step * step)
}

fn grid_ceil(ranges: &[(i64, i64, i64)], px: i64) -> Option<i64> {
    let f = grid_floor(ranges, px)?;
    if f == px {
        return Some(f);
    }
    let &(start, _, step) = ranges.iter().find(|(a, b, _)| *a <= f && f < *b).or_else(|| {
        ranges.last().filter(|(a, _, _)| f >= *a)
    })?;
    let _ = start;
    Some(f + step)
}

/// The quote pair a fair value implies, given inventory and the others' touch.
///
/// * `fair_c` — model price, cents.
/// * `pos_fp` — our signed YES position in `SIZE_SCALE` units. Long YES skews the pair **down**:
///   it raises the chance the next fill reduces us. This is A-S's reservation-price term, which
///   is the part of A-S that does not contain the unidentified `k`.
/// * `margin_c` — required edge per side, cents. A declared cost (fee + required profit), not a
///   fitted optimum: see the module note on why the A-S spread term is not available here.
/// * `skew_c` — cents of centre shift per whole contract of inventory.
/// * `bid_fp` / `ask_fp` — the others' touch. Used for two things only: post-only safety, and
///   the refusal rule.
///
/// The refusal rule is the whole gate. Bid at `r - margin` only if that is **at or above** the
/// others' bid — below it the order cannot fill, and at or above their bid is by construction
/// where our model says a bid still earns. Mirrored on the ask. There is no width term.
pub fn pair(
    fair_c: f64,
    pos_fp: i64,
    margin_c: f64,
    skew_c: f64,
    bid_fp: i64,
    ask_fp: i64,
    ranges: &[(i64, i64, i64)],
) -> Pair {
    if !fair_c.is_finite() || ranges.is_empty() || bid_fp >= ask_fp {
        return Pair::default();
    }
    let per_ct = pos_fp as f64 / crate::book::SIZE_SCALE as f64;
    let centre = fair_c - skew_c * per_ct;
    let to_fp = |c: f64| (c * (PRICE_SCALE as f64 / 100.0)).round() as i64;

    // The bid is the best price that still earns the margin, so it rounds DOWN to the grid; the
    // ask rounds UP. Rounding either one the other way pays the rounding out of the margin.
    let bid = grid_floor(ranges, to_fp(centre - margin_c))
        .filter(|&p| p >= bid_fp)
        // Post-only: never cross the others' ask. `tick_at` lives in `live`, and the step at the
        // ask is the one just below it, so clamp with the range the price lands in.
        .map(|p| p.min(ask_fp - step_below(ranges, ask_fp)))
        .filter(|&p| p > 0 && p >= bid_fp);
    let ask = grid_ceil(ranges, to_fp(centre + margin_c))
        .filter(|&p| p <= ask_fp)
        .map(|p| p.max(bid_fp + step_above(ranges, bid_fp)))
        .filter(|&p| p < PRICE_SCALE && p <= ask_fp);
    Pair { bid, ask }
}

/// Grid step just above `px` (for stepping a bid up off the others' bid).
fn step_above(ranges: &[(i64, i64, i64)], px: i64) -> i64 {
    ranges.iter().find(|(a, b, _)| *a <= px && px < *b).map_or(100, |(_, _, s)| *s)
}

/// Grid step just below `px` (for stepping an ask down off the others' ask). At 0.9000 that is
/// 1c, not the 0.1c above it: 0.8990 is not a price.
fn step_below(ranges: &[(i64, i64, i64)], px: i64) -> i64 {
    ranges.iter().find(|(a, b, _)| *a < px && px <= *b).map_or(100, |(_, _, s)| *s)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 0.1c-equivalent tolerance in probability.
    const TOL: f64 = 1e-3;

    fn mid_band_grid() -> Vec<(i64, i64, i64)> {
        // The crypto 15M taper: 0.1c below 10c, 1c to 90c, 0.1c above.
        vec![(0, 1_000, 10), (1_000, 9_000, 100), (9_000, 10_000, 10)]
    }

    #[test]
    fn norm_cdf_matches_known_values() {
        assert!((norm_cdf(0.0) - 0.5).abs() < 1e-9);
        assert!((norm_cdf(1.0) - 0.8413447).abs() < 1e-6);
        assert!((norm_cdf(-1.0) - 0.1586553).abs() < 1e-6);
        assert!((norm_cdf(1.959964) - 0.975).abs() < 1e-6);
        assert!((norm_cdf(-2.575829) - 0.005).abs() < 1e-6);
        // Symmetry and saturation.
        for x in [0.25, 0.5, 1.5, 3.0, 6.0, 11.0, 50.0] {
            assert!((norm_cdf(x) + norm_cdf(-x) - 1.0).abs() < 1e-7, "symmetry at {x}");
        }
        assert_eq!(norm_cdf(50.0), 1.0);
        assert_eq!(norm_cdf(-50.0), 0.0);
    }

    #[test]
    fn at_the_money_is_fifty_cents() {
        let p = digital_cents(2565.41, 2565.41, 900.0, annual_to_per_sqrt_s(0.27)).unwrap();
        assert!((p - 50.0).abs() < TOL, "{p}");
    }

    const STRIKE: f64 = 2565.41;

    /// `sigma_eff` in log units at `tau`, i.e. one unit of the only scale this contract has.
    fn sig_eff(tau: f64, annual: f64) -> f64 {
        annual_to_per_sqrt_s(annual) * (tau - 2.0 * SETTLE_AVG_S / 3.0).sqrt()
    }

    /// A level `k` standard deviations from the strike. Perturbing in *dollars* is the mistake
    /// that makes a test of this contract vacuous: `sigma_eff` is **14.1 bp** at `tau = 900 s`,
    /// so $35 on ETH is 9.5 sigma and prices to exactly 100.000c. Every probe here is in sigma.
    fn level_at(k: f64, tau: f64, annual: f64) -> f64 {
        STRIKE * (k * sig_eff(tau, annual)).exp()
    }

    #[test]
    fn monotone_in_level_and_in_time() {
        let s = annual_to_per_sqrt_s(0.27);
        let up = digital_cents(level_at(1.0, 900.0, 0.27), STRIKE, 900.0, s).unwrap();
        let flat = digital_cents(STRIKE, STRIKE, 900.0, s).unwrap();
        let down = digital_cents(level_at(-1.0, 900.0, 0.27), STRIKE, 900.0, s).unwrap();
        assert!(down < flat && flat < up, "{down} {flat} {up}");
        assert!((up - 84.134).abs() < 0.01, "one sigma up is Phi(1): {up}");
        // Above the strike, less time left means more certainty: the SAME level is further out
        // in sigma as tau shrinks, because sigma_eff shrinks.
        let level = level_at(1.0, 900.0, 0.27);
        let early = digital_cents(level, STRIKE, 900.0, s).unwrap();
        let late = digital_cents(level, STRIKE, 180.0, s).unwrap();
        assert!(late > early, "{late} !> {early}");
    }

    /// The averaging correction is first-order, not a refinement: it must remove exactly `2w/3`
    /// of variance, so a `tau` of 900 s must price as a terminal value at `900 - 40`.
    #[test]
    fn terminal_average_removes_two_thirds_of_a_window() {
        let s = annual_to_per_sqrt_s(0.27);
        let level = level_at(1.0, 900.0, 0.27);
        let with_avg = digital_cents(level, STRIKE, 900.0, s).unwrap();
        let expect = 100.0 * norm_cdf((level / STRIKE).ln() / sig_eff(900.0, 0.27));
        assert!((with_avg - expect).abs() < 1e-9);
        // And it is a material difference from pricing the close itself rather than the average.
        let naive = 100.0 * norm_cdf((level / STRIKE).ln() / (s * 900.0f64.sqrt()));
        assert!((with_avg - naive).abs() > 0.5, "correction worth only {}", with_avg - naive);
        // It is worth far more as tau shrinks toward the window: 40 s of 120 is a third.
        let l2 = level_at(1.0, 120.0, 0.27);
        let short_avg = digital_cents(l2, STRIKE, 120.0, s).unwrap();
        let short_naive = 100.0 * norm_cdf((l2 / STRIKE).ln() / (s * 120.0f64.sqrt()));
        assert!(
            (short_avg - short_naive).abs() > (with_avg - naive).abs(),
            "the correction must grow as tau approaches the averaging window"
        );
    }

    /// The governing sensitivity, pinned so a refactor cannot drift it silently. These are the
    /// numbers the margin and the whole seat are sized from.
    #[test]
    fn delta_and_latency_margin_are_the_documented_magnitudes() {
        let s = annual_to_per_sqrt_s(0.27);
        assert!((sig_eff(900.0, 0.27) * 1e4 - 14.10).abs() < 0.01, "sigma_eff at 900s");
        assert!((sig_eff(120.0, 0.27) * 1e4 - 4.30).abs() < 0.01, "sigma_eff at 120s");

        let d900 = delta_c_per_bp(STRIKE, STRIKE, 900.0, s).unwrap();
        let d120 = delta_c_per_bp(STRIKE, STRIKE, 120.0, s).unwrap();
        assert!((d900 - 2.83).abs() < 0.01, "{d900} c/bp at 900s");
        assert!((d120 - 9.28).abs() < 0.01, "{d120} c/bp at 120s");

        // Our measured reaction: index/print -> our change in the book, 11.6 ms.
        let m = latency_margin_c(STRIKE, STRIKE, 900.0, s, 0.0116).unwrap();
        assert!((m - 0.147).abs() < 0.001, "{m} c of latency toll at the money, 900s");
        // 59% of the measured real-print maker gross of +0.25 c/ct.
        assert!(m / 0.25 > 0.55 && m / 0.25 < 0.65, "toll is {:.0}% of gross", 100.0 * m / 0.25);
        // Late at the money it exceeds the whole gross, which is why the seat stops early.
        let late = latency_margin_c(STRIKE, STRIKE, 120.0, s, 0.0116).unwrap();
        assert!(late > 0.25, "{late} must exceed the +0.25c gross");
    }

    /// The gradient that inverts the width gate: the required margin carries `phi(z)`, so it is
    /// largest AT the money and falls away from it. A book-width gate has no such term, which is
    /// why it was measured flat (+0.249..+0.244 c/ct from a 1c to a 10c book).
    #[test]
    fn required_margin_is_largest_at_the_money() {
        let s = annual_to_per_sqrt_s(0.27);
        let at = latency_margin_c(STRIKE, STRIKE, 900.0, s, 0.0116).unwrap();
        let one = latency_margin_c(level_at(1.0, 900.0, 0.27), STRIKE, 900.0, s, 0.0116).unwrap();
        let two = latency_margin_c(level_at(2.0, 900.0, 0.27), STRIKE, 900.0, s, 0.0116).unwrap();
        assert!(at > one && one > two, "{at} {one} {two}");
        // Two sigma out it is under a fifth of the at-the-money toll, and under the gross.
        assert!(two < 0.2 * at, "{two} vs {at}");
        assert!(two < 0.25, "{two} must sit under the +0.25c gross");
        // Symmetric in the wings.
        let down = latency_margin_c(level_at(-2.0, 900.0, 0.27), STRIKE, 900.0, s, 0.0116).unwrap();
        assert!((down - two).abs() < 1e-9);
    }

    /// ⚑ The precision wall, pinned. At the money the level toll dwarfs the latency toll and the
    /// whole available gross, which is the measured reason a model-priced maker cannot compete
    /// with a 1c book here.
    #[test]
    fn level_toll_dominates_the_latency_toll_at_the_money() {
        let s = annual_to_per_sqrt_s(0.27);
        let lat = latency_margin_c(STRIKE, STRIKE, 900.0, s, 0.0116).unwrap();
        // 1 bp is the measured agreement between the mid-implied level and our own spot.
        let lvl = level_margin_c(STRIKE, STRIKE, 900.0, s, 1.0).unwrap();
        assert!((lvl - 2.83).abs() < 0.01, "{lvl} c for 1 bp of level error");
        assert!(lvl / lat > 15.0, "level toll is only {:.1}x the latency toll", lvl / lat);
        // And it is more than ten times the whole measured maker gross.
        assert!(lvl / 0.25 > 10.0, "{:.1}x the +0.25c gross", lvl / 0.25);
    }

    /// The gate that replaces width: a band in `|z|`, derived, no fitted parameter. At the money
    /// it refuses; far enough into the wing it admits — and the band it admits is the deci-tick
    /// wing, which is where the only Kalshi crypto cell that ever made real money lives.
    #[test]
    fn quotable_is_a_band_in_z_and_it_is_the_wing() {
        let s = annual_to_per_sqrt_s(0.27);
        let q = |k: f64| quotable(level_at(k, 900.0, 0.27), STRIKE, 900.0, s, 0.0116, 1.0, 0.25).unwrap();

        let (at_ok, at_need) = q(0.0);
        assert!(!at_ok, "at the money must refuse: needs {at_need:.2}c against 0.25c");
        assert!(at_need > 2.9, "{at_need}");

        // Monotone in |z|: the requirement falls as phi(z) does.
        let needs: Vec<f64> = [0.0, 1.0, 2.0, 2.5, 3.0].iter().map(|&k| q(k).1).collect();
        for w in needs.windows(2) {
            assert!(w[1] < w[0], "required margin must fall into the wing: {needs:?}");
        }
        // Symmetric.
        assert!((q(-2.5).1 - q(2.5).1).abs() < 1e-9);

        // Somewhere out there it admits, and where it does the contract is a wing contract.
        let first_ok = (0..60).map(|i| i as f64 / 10.0).find(|&k| q(k).0);
        let k = first_ok.expect("the band must be non-empty");
        let price = digital_cents(level_at(k, 900.0, 0.27), STRIKE, 900.0, s).unwrap();
        assert!(k >= 2.0, "admitted at only {k} sigma");
        assert!(price > 95.0 || price < 5.0, "admitted at {price}c, which is not a wing price");
    }

    /// A caller that claims more gross gets a wider band — the gate is a real inequality, not a
    /// relabelled constant. Stated as a test because a gate that cannot fail is decoration.
    #[test]
    fn quotable_widens_with_the_gross_it_is_given() {
        let s = annual_to_per_sqrt_s(0.27);
        let band = |gross: f64| (0..60).map(|i| i as f64 / 10.0)
            .find(|&k| quotable(level_at(k, 900.0, 0.27), STRIKE, 900.0, s, 0.0116, 1.0, gross).unwrap().0)
            .unwrap();
        // A 1c half-spread admits closer to the money than a 0.25c gross does.
        assert!(band(1.0) < band(0.25), "{} !< {}", band(1.0), band(0.25));
        // And a perfectly known level (0 bp) is admitted on the latency toll alone, much closer in.
        let perfect = (0..60).map(|i| i as f64 / 10.0)
            .find(|&k| quotable(level_at(k, 900.0, 0.27), STRIKE, 900.0, s, 0.0116, 0.0, 0.25).unwrap().0)
            .unwrap();
        assert!(perfect < band(0.25), "level precision must be what binds: {perfect} vs {}", band(0.25));
        assert_eq!(perfect, 0.0, "at 0 bp of level error the at-the-money latency toll alone fits in 0.25c");
    }

    #[test]
    fn latency_margin_refuses_the_same_inputs_the_price_does() {
        let s = annual_to_per_sqrt_s(0.27);
        assert!(latency_margin_c(STRIKE, STRIKE, 60.0, s, 0.0116).is_none());
        assert!(latency_margin_c(STRIKE, STRIKE, 900.0, 0.0, 0.0116).is_none());
        assert!(latency_margin_c(STRIKE, STRIKE, 900.0, s, 0.0).is_none());
        assert!(latency_margin_c(0.0, STRIKE, 900.0, s, 0.0116).is_none());
        assert!(delta_c_per_bp(STRIKE, STRIKE, 30.0, s).is_none());
    }

    #[test]
    fn refuses_inside_the_settlement_window() {
        let s = annual_to_per_sqrt_s(0.27);
        assert!(digital_cents(2600.0, 2565.41, 60.0, s).is_none());
        assert!(digital_cents(2600.0, 2565.41, 39.0, s).is_none());
        assert!(digital_cents(2600.0, 2565.41, 61.0, s).is_some());
    }

    #[test]
    fn refuses_bad_inputs_rather_than_defaulting_to_the_mid() {
        let s = annual_to_per_sqrt_s(0.27);
        assert!(digital_cents(0.0, 2565.41, 900.0, s).is_none());
        assert!(digital_cents(2600.0, 0.0, 900.0, s).is_none());
        assert!(digital_cents(2600.0, 2565.41, 900.0, 0.0).is_none());
        assert!(digital_cents(f64::NAN, 2565.41, 900.0, s).is_none());
        assert!(digital_cents(2600.0, 2565.41, 900.0, f64::INFINITY).is_none());
    }

    #[test]
    fn vol_recovers_a_known_constant() {
        // A deterministic 1 bp move every second is a per-root-second vol of 1e-4.
        let mut v = Vol::new(1_000_000, 120.0);
        let mut px = 100.0f64;
        for i in 0..600 {
            v.push(i * 1_000_000, px);
            px *= if i % 2 == 0 { 1.0001 } else { 1.0 / 1.0001 };
        }
        let s = v.sigma(30, 0.0, 1.0).unwrap();
        assert!((s - 1e-4).abs() < 2e-6, "{s}");
    }

    /// A stalled publisher must contribute its variance once, not once per grid step it spans:
    /// otherwise a reconnect reads as a volatility spike and widens every quote.
    #[test]
    fn vol_scales_a_gap_rather_than_counting_it_once_per_step() {
        let mut dense = Vol::new(1_000_000, 1e9);
        let mut sparse = Vol::new(1_000_000, 1e9);
        // Same total variance: 100 one-second 1bp moves vs 10 ten-second sqrt(10)bp moves.
        let mut px = 100.0;
        for i in 0..=100 {
            dense.push(i * 1_000_000, px);
            px *= 1.0 + 1e-4;
        }
        let mut px = 100.0;
        for i in 0..=10 {
            sparse.push(i * 10_000_000, px);
            px *= 1.0 + 1e-4 * 10.0f64.sqrt();
        }
        let (d, s) = (dense.sigma(5, 0.0, 1.0).unwrap(), sparse.sigma(5, 0.0, 1.0).unwrap());
        assert!((d - s).abs() / d < 0.02, "dense {d} vs sparse {s}");
    }

    #[test]
    fn vol_clamps_and_withholds() {
        let mut v = Vol::new(1_000_000, 120.0);
        assert!(v.sigma(10, 0.0, 1.0).is_none(), "no opinion before min_samples");
        let mut px = 100.0f64;
        for i in 0..50 {
            v.push(i * 1_000_000, px);
            px *= 1.05; // absurd
        }
        let ceil = annual_to_per_sqrt_s(5.0);
        assert_eq!(v.sigma(10, 0.0, ceil).unwrap(), ceil);
        let floor = annual_to_per_sqrt_s(0.1);
        assert_eq!(v.sigma(10, floor, ceil).unwrap().max(floor), v.sigma(10, floor, ceil).unwrap());
    }

    #[test]
    fn anchored_level_carries_the_index_by_the_spot_return() {
        let t = IndexTick { value: 2565.41, source_us: 0, recv_us: 38_000 };
        let l = anchored_level(&t, 20.0).unwrap(); // +20 bps
        assert!((l - 2565.41 * 1.002).abs() < 1e-9, "{l}");
        assert!((anchored_level(&t, 0.0).unwrap() - 2565.41).abs() < 1e-12);
        assert!(anchored_level(&t, f64::NAN).is_none());
        assert!(anchored_level(&IndexTick { value: 0.0, ..t }, 0.0).is_none());
    }

    #[test]
    fn grid_rounds_into_the_taper_not_past_it() {
        let g = mid_band_grid();
        // Mid band: 1c steps.
        assert_eq!(grid_floor(&g, 4_350), Some(4_300));
        assert_eq!(grid_ceil(&g, 4_350), Some(4_400));
        assert_eq!(grid_floor(&g, 4_300), Some(4_300));
        assert_eq!(grid_ceil(&g, 4_300), Some(4_300));
        // Wing: 0.1c steps.
        assert_eq!(grid_floor(&g, 955), Some(950));
        assert_eq!(grid_ceil(&g, 951), Some(960));
        assert_eq!(grid_floor(&g, 9_505), Some(9_500));
    }

    /// The headline behaviour: on a 1c book with the model at the mid we quote BOTH sides at the
    /// touch, with no width requirement anywhere.
    #[test]
    fn agreement_quotes_two_sided_in_a_one_cent_book() {
        let g = mid_band_grid();
        let p = pair(43.5, 0, 0.3, 0.0, 4_300, 4_400, &g);
        assert_eq!(p.bid, Some(4_300));
        assert_eq!(p.ask, Some(4_400));
    }

    /// And the behaviour a width gate can never have: when the model disagrees with the mid by
    /// more than the margin, the seat goes ONE-sided instead of quoting a price it does not
    /// believe. This is the refusal that un-blinds a two-sided maker.
    #[test]
    fn disagreement_quotes_one_sided() {
        let g = mid_band_grid();
        // Model below the book: their bid is through fair, so there is no bid of ours.
        let low = pair(43.1, 0, 0.3, 0.0, 4_300, 4_400, &g);
        assert_eq!(low.bid, None, "must refuse a bid at or above fair - margin");
        assert_eq!(low.ask, Some(4_400));
        // Model above the book: mirrored.
        let high = pair(43.9, 0, 0.3, 0.0, 4_300, 4_400, &g);
        assert_eq!(high.bid, Some(4_300));
        assert_eq!(high.ask, None);
    }

    /// A wide book is quoted *inside*, at the model's own price — the point of dropping the width
    /// gate is that width changes where we quote, never whether.
    #[test]
    fn a_wide_book_is_quoted_inside_at_the_model_price() {
        let g = mid_band_grid();
        let p = pair(50.0, 0, 2.0, 0.0, 4_000, 6_000, &g);
        assert_eq!(p.bid, Some(4_800));
        assert_eq!(p.ask, Some(5_200));
    }

    #[test]
    fn inventory_skews_the_centre_toward_reducing() {
        let g = mid_band_grid();
        let flat = pair(50.0, 0, 2.0, 1.0, 4_000, 6_000, &g);
        // Long one YES contract: both quotes move down by the skew, so the ask gets easier to
        // fill and the bid harder.
        let long = pair(50.0, crate::book::SIZE_SCALE, 2.0, 1.0, 4_000, 6_000, &g);
        assert_eq!(long.bid, Some(4_700));
        assert_eq!(long.ask, Some(5_100));
        assert!(long.bid < flat.bid && long.ask < flat.ask);
        let short = pair(50.0, -crate::book::SIZE_SCALE, 2.0, 1.0, 4_000, 6_000, &g);
        assert_eq!(short.bid, Some(4_900));
        assert_eq!(short.ask, Some(5_300));
    }

    /// Post-only is a hard invariant: a model far from the book must never produce a crossing
    /// price. 1,649 `invalid_price` rejects on run 9 came from the neighbouring mistake.
    #[test]
    fn never_crosses_the_others_touch() {
        let g = mid_band_grid();
        // Fair miles above the book: the ask would want to sit below their bid.
        let p = pair(90.0, 0, 2.0, 0.0, 4_300, 4_400, &g);
        assert_eq!(p.bid, Some(4_300), "clamped to post-only under their ask");
        assert!(p.ask.is_none_or(|a| a > 4_300));
        // Fair miles below: the bid would want to sit above their ask.
        let q = pair(10.0, 0, 2.0, 0.0, 4_300, 4_400, &g);
        assert!(q.bid.is_none_or(|b| b < 4_400));
        assert_eq!(q.ask, Some(4_400));
    }

    #[test]
    fn refuses_a_crossed_or_empty_book() {
        let g = mid_band_grid();
        assert_eq!(pair(50.0, 0, 1.0, 0.0, 5_000, 5_000, &g), Pair::default());
        assert_eq!(pair(50.0, 0, 1.0, 0.0, 5_100, 5_000, &g), Pair::default());
        assert_eq!(pair(f64::NAN, 0, 1.0, 0.0, 4_300, 4_400, &g), Pair::default());
        assert_eq!(pair(50.0, 0, 1.0, 0.0, 4_300, 4_400, &[]), Pair::default());
    }

    /// Prices must stay strictly inside (0, 100): a saturated model is exactly where a quote at
    /// 0 or 100 would be free money for the other side.
    #[test]
    fn never_quotes_at_or_through_the_bounds() {
        let g = mid_band_grid();
        for fair in [-10.0, 0.0, 0.05, 99.95, 100.0, 140.0] {
            let p = pair(fair, 0, 1.0, 0.0, 10, 9_990, &g);
            assert!(p.bid.is_none_or(|b| b > 0 && b < PRICE_SCALE), "bid at fair {fair}");
            assert!(p.ask.is_none_or(|a| a > 0 && a < PRICE_SCALE), "ask at fair {fair}");
        }
    }
}
