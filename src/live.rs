//! ARMED market maker. Places REAL orders. Same quoting rule as `shadow` (gate: join the touch
//! on both sides of mid-band markets, pull a side when 1 s momentum runs into it or it is nearly
//! empty), clip `--clip` (default 1), |position| <= max_pos per market, flat-or-hold to settlement (never crosses).
//!
//! Purpose of the first runs: measure REAL fills — in particular how much of the queue ahead of
//! us at join time clears by CANCELS rather than trades (the shadow assumed none did).
//!
//! Guardrails (each one a past incident in this repo's memory):
//! - orders keyed by client_order_id, never by ticker;
//! - position from the venue's own fill messages (`post_position_fp`), not from our arithmetic;
//! - session loss cap on the engine's OWN fill ledger marked to mid (a pair is worth exactly 100
//!   at any mid — venue position fields net pairs away), PLUS a cumulative cap anchored to a
//!   venue baseline persisted across restarts in `live_state.json`;
//! - every order in an order group whose contracts_limit makes the VENUE cancel us on a sweep;
//! - posts gated on time-to-close at post time, resting orders pulled before close;
//! - venue rejections are counted, never fatal; any feed break cancels everything first;
//! - on exit (deadline, cap, ctrl-c, error) cancel all, then verify nothing rests.

use std::{
    collections::{HashMap, VecDeque},
    fs::{self, File, OpenOptions},
    io::{BufWriter, Write},
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant},
};

use anyhow::{Context, Result, bail};
use flate2::{Compression, write::GzEncoder};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::{sync::mpsc, time::interval};
use tokio_tungstenite::tungstenite::Message;

use crate::{
    auth::Auth,
    book::{Book, PRICE_SCALE, SIZE_SCALE, parse_value},
    client, rest_base, rfc3339_us, signed_ws_request,
    stats::Samples,
    unix_us,
};

/// Per-interval cap for the stage timers. Reset every housekeeping tick, so this only has to
/// hold one interval's frames, not a whole session.
const TIMING_CAP: usize = 50_000;

pub struct LiveParams {
    pub series: Vec<String>,
    pub minutes: u64,
    pub max_pos_fp: i64,
    /// Contracts per order (SIZE_SCALE units). |position| + clip must stay within max_pos.
    pub clip_fp: i64,
    pub session_max_loss_c: f64,
    pub cumulative_max_loss_c: f64,
    pub group_contracts_per_15s: i64,
    pub stop_before_close_s: i64,
    pub mid_lo_c: f64,
    pub mid_hi_c: f64,
    pub mom_pull_c: f64,
    pub thin_pull: f64,
    pub exchange_index: i64,
    pub out: PathBuf,
    /// 0 = join the touch (mid band). n > 0 = PENNY (PREREG_penny.md): one tick inside the
    /// OTHERS' touch, any band 1-99 c, only when their spread is at least n ticks.
    pub penny_room: i64,
    /// ⚑ MODEL PRICING. Quote **continuously** off an independent fair value (`fairvalue`)
    /// instead of gating on the book's width. When set, `penny_room` and `book_residual` play no
    /// part in the entry decision: the price comes from the settlement index, the strike, the
    /// time to close and realised vol, and the decision to quote a side is the model's own
    /// refusal rule (bid only at or above `fair − margin`), which carries no width term.
    ///
    /// Three measurements say the width gate is the wrong control on this panel:
    /// 1. **It buys nothing.** Real-print maker gross is flat at +0.249 / +0.264 / +0.252 /
    ///    +0.240 / +0.244 c/ct across 0-1, 1-2, 2-3, 3-5, 5-10 c of spread, on 8,038,551 prints
    ///    (`kalshi-scalp/FINDINGS_real_print_ledger.md`). There is no width gradient to gate on.
    ///    The gradient this gate was built from came from a fill model that overstated adverse
    ///    selection **5.17×** (7.343 sim vs 1.421 real).
    /// 2. **It costs the exits.** See `exit_ignore_room` below: a 4c mid-band spread occurs in
    ///    **5-6% of states**, so the gate silences ~94% of the seat, and the −$6.18 settlement
    ///    loss of the 2026-10-07 armed session was in positions whose profitable exit print did
    ///    arrive (95.5% of them) while we had no order resting there.
    /// 3. **A two-sided quote with no model is blind to a mispricing by construction** — at an
    ///    even side split the calibration coefficient is exactly zero
    ///    (`kalshi-scalp/FINDINGS_tail_seller_surface.md`). Only an independent price can decide
    ///    to quote one side and refuse the other.
    pub fair: bool,
    /// Cents of margin **on top of** the derived latency toll `fairvalue::latency_margin_c`,
    /// i.e. the required profit and the maker fee. The toll itself is not a parameter: it is
    /// `phi(z)·sigma·sqrt(reaction)/sigma_eff`, ~0.147 c at the money at `tau = 900 s` for our
    /// measured 11.6 ms reaction, and it is largest at the money — the opposite shape to a
    /// width gate.
    pub fair_margin_c: f64,
    /// Our index-to-book reaction time, seconds, which is what the latency toll is computed
    /// from. Measured 11.6 ms for post and 10.6 ms for cancel
    /// (`kalshi-15m-crypto-competitors-react-twice-as-fast-as-the-ohio-box`). Raise it, never
    /// lower it below a measurement: this term is the seat's whole cost of being stale.
    pub fair_reaction_s: f64,
    /// Cents the quoted centre shifts per whole contract of inventory — A-S's reservation-price
    /// term, the half of A-S whose parameter is identified here. The spread term is NOT used:
    /// its `k` is unidentified on this venue (hazard ratio inverts past 8 s of resting, `k`
    /// negative in 3 of 8 age bins, implied optimum spans 1.65c to undefined and brackets what
    /// the venue already quotes) and it collapses to `1/k` γ-free on a 1c lattice anyway
    /// (`as-arrival-decay-k-is-not-identified`).
    pub fair_skew_c: f64,
    /// Vol floor and ceiling, as annualised fractions, clamped around the realised EWMA. The vol
    /// is the one input that can drive fair value to 0 or 100 and quote a whole ladder at the
    /// wings, so it is bounded rather than trusted. 27% is the measured 15M conditional vol.
    pub fair_vol_floor: f64,
    pub fair_vol_ceil: f64,
    /// EWMA half-life in samples (= seconds at the 1 s sampling grid) and the minimum samples
    /// before the vol has an opinion at all. Below the minimum, no model quote is produced.
    pub fair_vol_half_life: f64,
    pub fair_vol_min_samples: u32,
    /// Refuse to price when the last index frame is older than this (µs). The index is 26-73 ms
    /// late by construction (`FINDINGS_altfeed_index_path_20261007.md`); this catches a stalled
    /// or unsubscribed channel, which would otherwise leave the level anchored in the past while
    /// spot carried it forward on a return the index never confirmed.
    pub fair_max_index_age_us: i64,
    /// Requote threshold in cents: amend only when the model price has moved at least this far
    /// from the resting price. 0 would chase every index tick at 10 tokens an amend. At the
    /// measured 2.83 c/bp this is also the quote's resolution in spot terms.
    pub fair_requote_c: f64,
    /// ⚑ How stale our index level is, as a level error in basis points, and what the seat can
    /// earn, in cents. Together these are the binding gate.
    ///
    /// **The required margin is `sigma`-free** (measured 2026-10-07,
    /// `FINDINGS_fair_value_precision_wall_20261007.md`): `delta = 100·phi(z)/sigma_eff` while the
    /// level error is `sigma·sqrt(staleness)`, so the product is
    /// `100·phi(z)·sqrt(staleness/(tau−40))` with no volatility term at all. It cannot be improved
    /// by a calmer asset, a calmer hour, or a better vol model — only by freshness and by time
    /// left. Measured staleness (`transport + gap/2`, the cycle average): **138 ms** on the 200 ms
    /// indices (BTC ETH SOL XRP DOGE), **538 ms** on the 1 s ones (BNB HYPE NEAR ZEC), giving a
    /// level error of **0.15-0.42 bp** and **0.40-1.54 bp** respectively.
    ///
    /// **`--fair-gross-c` declares WHICH INCOME is being claimed**, and the answer is now measured:
    /// - **0.25 (default)** — the real-print gross held to settlement. At the money this costs
    ///   **0.65 c against 0.25 c, 2.6× over**, and admits only `|z|` outside **8.3c / 91.7c** at
    ///   `tau` = 900. Conservative, and the honest default.
    /// - ⛔ **1.00 — REFUTED, do not set** (`../kalshi-scalp/FINDINGS_pair_rate_20261007.md`,
    ///   8.04M real prints, 63 day clusters). The paired round trip does **not** net 1.00 c. The
    ///   pair rate is achievable — **99.14%** at a capture ceiling that does not exist, against a
    ///   97.3% break-even — but **c/pair falls as the pair rate rises**: +2.12 c/pair at an 85%
    ///   rate, **−1.27 c/pair at 99%**. Best of 11 scored cells is **−1.36 c/opening**, and
    ///   **0 of 8 series are positive in any cell**. The mechanism: `|q| <= 1` on a 15-minute
    ///   binary sells a 1.3 c spread against a ~23%-probability **−46 c** tail, and crossing out
    ///   recovers only 0.52 c of a 10.5 c loss because the naked loss is already realised in the
    ///   price. The exit policy relocates the loss; it does not reduce it.
    ///
    /// So the seat is closed in the **mid band** on all three legs — the width gate it replaced was
    /// empty, its level precision is fine, and its pair rate is reachable and still does not pay.
    /// What survives is the wing, where both tolls collapse with `phi(z)`. The armed session said
    /// the same thing in miniature: pairs +$0.42, settlement-held −$6.18.
    ///
    /// `--fair-level-precision-bp 0` disables the level term, asserting the level is known
    /// exactly. It is not, and such a run is not evidence about a model-priced seat.
    pub fair_level_precision_bp: f64,
    pub fair_gross_c: f64,
    /// Join one 15-minute crypto touch only when it remains favorable versus the receipt-clock
    /// midpoint from one second ago. This is the book-only residual control.
    pub book_residual: bool,
    /// No order that would OPEN or ADD to a position this close to the market's close; orders
    /// that reduce the position keep quoting until `stop_before_close_s`. 450 s cut live
    /// leftovers 126 → 15 ct over 197 markets (`leftover_rules.py`, 2026-09-25).
    pub open_cutoff_s: i64,
    /// Requote an untouched resting order with one amend (same order_id, measured 2026-09-26)
    /// instead of cancel + create.
    pub amend: bool,
    /// AMEND-ONLY: never cancel-and-repost when an amend can express the same intent, including
    /// the spot pull. Measured on the az2 box 2026-10-06: amend -> in book **2.6-2.9 ms**,
    /// create 2.7-3.0, cancel 4.79 — so a pull by amend is one round trip at the fastest of the
    /// three, where cancel-then-repost is two. A cancel still happens where an amend cannot say
    /// it: a partial fill (amend's `count` semantics there are unmeasured), a price outside the
    /// grid, and every shutdown path. A risk control that cannot be expressed is not skipped.
    pub amend_only: bool,
    /// Amend-only pull distance, in ticks away from the touch on our own side. The order stays
    /// alive and out of the way instead of being cancelled and reposted.
    pub pull_amend_ticks: i64,
    /// Backstop only for the post hold after a fill; the `fill` message releases it (see `Mkt`).
    pub fill_hold_us: i64,
    /// ⚑ AN EXIT IS NOT AN ENTRY. Measured 2026-10-07 on the 544 fills of a −$5.76 armed session:
    /// pairs earned +$0.42 and every cent of the loss (−$6.18) was in positions held to
    /// settlement — yet **95.5% of those had a profitable exit print arrive later**, a mean of
    /// 9,875 contracts through our own break-even at 11.32c better than entry. We were not short
    /// of a counterparty; we had no order there, because the reducing leg had to clear both entry
    /// gates: a `penny_room`-tick spread (4c in the mid band, which occurs in 5–6% of states) and
    /// `stop_before_close_s`, i.e. the first half of a 900 s market. The sports path has always
    /// exempted the flattening side (`if exiting { sports_ok }`); these three give the crypto seat
    /// the same, and all default to the old behaviour.
    ///
    /// Width gate off for the reducing leg: never require a wide book to get out.
    pub exit_ignore_room: bool,
    /// The reducing leg's own close buffer, in seconds. Must stay large enough to cancel safely,
    /// but 450 s means going silent at a 15-minute market's halfway mark while holding inventory.
    pub exit_stop_before_close_s: i64,
    /// Minimum cents of edge an exit may rest at, measured from the opening fill's price: the
    /// "wait until we are profitable" clamp. Negative = off (rest wherever the touch is, which can
    /// lock a loss). 0 = never rest through break-even.
    pub exit_min_edge_c: f64,
    /// Pull the side a spot move of more than this many bps over `spot_window_us` runs into, on
    /// the spot tick itself. 0 = off.
    pub spot_bps: f64,
    /// Spot venues to read. Several, because one venue's `ticker` channel only fires on that
    /// venue's own matches: on a thin altcoin Coinbase can hold a stale print for minutes while
    /// the quote moves everywhere else. See `fastspot` and `altfeed_score.py`.
    pub spot_venues: Vec<crate::fastspot::Venue>,
    /// The move is the MEDIAN of the per-venue returns, and needs at least this many venues
    /// fresh. Below it there is no signal rather than a one-venue signal: a two-venue median is
    /// one venue plus a tiebreak.
    pub spot_min_venues: usize,
    /// A venue whose last quote is older than this does not vote.
    pub spot_max_age_us: i64,
    /// Return window the pull threshold is measured over.
    pub spot_window_us: i64,
    /// Cap on the ROUND's directional bet: the summed YES position across every market closing
    /// at the same time (the crypto series move together, so it is one bet). A side that would
    /// push |sum| past this is not quoted; sides that shrink it always are. 0 = off. Replaying
    /// all 11 live penny journals (`leftover_portfolio.py`, 2026-09-28) at 2 ct: +19 +/- 7 c/round
    /// vs base, max drawdown $9.96 -> $3.6, robust to 5 s of fill-knowledge lag.
    pub max_round_net_fp: i64,
    /// Opening/adding orders only at a YES price in [open_lo_c, open_hi_c); orders that reduce the
    /// position quote at any price. Wing fills (<10c, >90c) were 60% of 2,960 capped live fills
    /// and settled at ~0 c/ct in both halves (2026-09-30), only adding variance. 0/100 = off.
    pub open_lo_c: f64,
    pub open_hi_c: f64,
    /// Sports mode: many markets per series, game-level pauses off the parent (full-game) books,
    /// per-game and total worst-case contract caps. None = the 15M crypto engine.
    pub sports: Option<SportsLive>,
}

pub struct SportsLive {
    /// Only tickers containing `-<tag>` for one of these (game dates like 26SEP27).
    pub tags: Vec<String>,
    /// Full-game series watched as a score feed and never quoted (e.g. KXNCAAFGAME,KXNCAAFTOTAL).
    pub parents: Vec<String>,
    /// Quote only when the OTHERS' spread is at least this wide (cents), one tick inside it.
    pub min_spread_c: i64,
    /// Pause a game when any of its parent books' mid moves this much (cents) within the window.
    pub parent_move_c: f64,
    pub parent_window_us: i64,
    /// Pause a game when one of its quoted books' mid jumps this much (cents) within 2 s.
    pub jump_c: f64,
    pub pause_us: i64,
    /// Worst-case contracts per game / in total, assuming every resting order of one side fills
    /// (a score sweeps a whole strike ladder the same way).
    pub max_game_fp: i64,
    pub max_total_fp: i64,
    pub max_markets: usize,
    pub refresh_s: u64,
    /// Go one tick inside when the others' spread is at least this (cents); below it, join.
    pub penny_min_c: i64,
    /// Open only on books at most this wide (cents): a fill on a wider book is a directional
    /// entry with no realistic second leg.
    pub max_open_spread_c: i64,
    /// Round trip: after an opening fill, only the flattening side quotes, at entry +/- this
    /// edge (cents) until `scratch_us`, then at the entry price until `bail_us`, then at the
    /// competitive price whatever it costs. Always post-only; never crosses.
    pub exit_edge_c: i64,
    pub scratch_us: i64,
    pub bail_us: i64,
    /// `--series auto`: discover across every fee-free sports series (this set), in the
    /// background, ranked by 24h volume; tags are the rolling UTC dates (yesterday..tomorrow).
    pub auto_series: Option<std::collections::HashSet<String>>,
    pub min_v24: f64,
}

/// --dry-run: no order-group create, no order, amend or cancel is sent; acks are synthesized
/// locally so the whole decision path (discovery, pauses, caps) runs against the live feed.
pub static DRY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
fn dry() -> bool { DRY.load(std::sync::atomic::Ordering::Relaxed) }

const BID: usize = 0;
const ASK: usize = 1;

fn book_residual_allows(side: usize, target_fp: i64, anchor_c: f64) -> bool {
    if side == BID { anchor_c >= target_fp as f64 / 100.0 }
    else { target_fp as f64 / 100.0 >= anchor_c }
}

fn opening_deadline_ok(remaining: Duration, cutoff_s: i64) -> bool {
    cutoff_s <= 0 || remaining > Duration::from_secs(cutoff_s as u64)
}

fn open_band_ok(target_fp: i64, lo_c: f64, hi_c: f64) -> bool {
    let c = target_fp as f64 / 100.0;
    c >= lo_c && c < hi_c
}

fn nonnegative_pair_price(position_fp: i64, side: usize, target_fp: i64, entry_fp: i64) -> bool {
    if position_fp > 0 && side == ASK { target_fp >= entry_fp }
    else if position_fp < 0 && side == BID { target_fp <= entry_fp }
    else { true }
}

#[derive(Clone, Copy, PartialEq, Debug)]
enum St {
    PendingNew,
    Resting,
    PendingCancel,
    /// Amend in flight: still resting at `price` until the ack.
    PendingAmend,
}

struct LiveOrder {
    coid: String,
    order_id: Option<String>,
    ticker: String,
    side: usize,
    price: i64,
    st: St,
    /// Unfilled size resting at the venue, from `remaining_count_fp`. A half-filled clip-2 order
    /// holds 1 ct at our price, and only that much is ours to remove when reading the others' book.
    remaining_fp: i64,
}

/// Would a clip on this side push the round's summed YES position past the cap? A side that
/// shrinks |sum| is never blocked, so a round over the cap can always work its way back.
/// Where a reducing order may rest: never through break-even, and never crossing the others'
/// touch. `entry_px` is the opening fill's price in PRICE_SCALE units (exact at `--max-pos 1`);
/// `min_edge_c` below zero disables the clamp and the exit simply chases the touch, which is what
/// booked a certain loss on a seat built to collect a spread. Walking back to `bid + tick` /
/// `ask - tick` keeps the order post-only even in a one-tick book.
fn exit_target(side: usize, target: i64, entry_px: i64, min_edge_c: f64,
               bid: i64, ask: i64, tick: i64) -> i64 {
    if min_edge_c < 0.0 || entry_px <= 0 {
        return target;
    }
    let edge = (min_edge_c * 100.0).round() as i64;
    if side == ASK {
        target.max(entry_px + edge).max(bid + tick)
    } else {
        target.min(entry_px - edge).min(ask - tick)
    }
}

/// `user_order` says a fill happened and frees the order slot; the `fill` message carries
/// `post_position_fp`, the position authority. Posting between the two can quote off a stale
/// position, which is how NEAR reached +2 on 2026-09-25. Hold until the authority lands, with a
/// timer only as the backstop for a `fill` that never arrives or fails to parse.
fn announce_fill(mk: &mut Mkt, now: i64, backstop_us: i64) {
    mk.fills_announced += 1;
    if mk.fills_announced > mk.fills_seen {
        mk.hold_until_us = now + backstop_us;
    }
}

/// The authority landed. Release only when every announced fill has been accounted for, so a
/// second fill arriving mid-hold cannot be released by the first one's message.
fn observe_fill(mk: &mut Mkt) {
    mk.fills_seen += 1;
    if mk.fills_seen >= mk.fills_announced {
        mk.hold_until_us = 0;
    }
}

fn round_cap_blocks(round_net_fp: i64, is_bid: bool, clip_fp: i64, cap_fp: i64) -> bool {
    let after = round_net_fp + if is_bid { clip_fp } else { -clip_fp };
    after.abs() > cap_fp && after.abs() > round_net_fp.abs()
}

#[derive(Default)]
struct Mkt {
    book: Option<Book>,
    close_unix_ms: i64,
    mids: VecDeque<(i64, f64)>,
    receipt_mids: VecDeque<(i64, f64)>,
    pos_fp: i64,
    /// Our own fill ledger for this market, this run: contracts of YES and NO bought and the net
    /// cash flow (cents). Marked at `last_mid`, a YES+NO pair is worth 100 at any mid.
    yes_ct: f64,
    no_ct: f64,
    flow_c: f64,
    last_mid: f64,
    slots: [Option<String>; 2], // coid per side
    /// No posting in this market until then: after any fill, until the venue's `fill` message
    /// (the position authority) has landed. The `user_order` "executed" message can arrive first,
    /// free the slot, and let a new order post on a stale position (NEAR reached +2, 2026-09-25).
    /// ⚑ This is now a BACKSTOP, not the mechanism. Measured on run 12 (1,089 fills matched on
    /// client_order_id): the `fill` message arrives BEFORE its `user_order` 14.9% of the time, and
    /// when it is later it is p50 0.136 ms / p99 1.60 ms. The old flat 1.5 s timer was therefore
    /// over-provisioned ~11,000x and cost 1,634 s of silence per run — during which 554 prints
    /// landed at our just-filled price. Holding on the ANNOUNCED-vs-SEEN counters below instead
    /// enforces the same invariant for 2.3 s total, recovering 99.86% of it.
    hold_until_us: i64,
    /// Fills announced by `user_order` vs fills whose `fill` message has been applied. The two
    /// channels race in both directions, so a counter pair is the only ordering-safe form: hold
    /// while `fills_announced > fills_seen`.
    fills_announced: u64,
    fills_seen: u64,
    /// The venue's price grid, (start, end, step) in PRICE_SCALE units, from `price_ranges`.
    /// Crypto and GOLD/SILVER/WTI step 0.1c below 10c and above 90c; COPPER/NATGAS step 1c
    /// everywhere, so the wing tick must never be assumed.
    ranges: Vec<(i64, i64, i64)>,
    /// Sports: the game key (ticker's second token) and whether this is a watched parent book.
    game: String,
    parent: bool,
    /// Sports: opening fill price (PRICE_SCALE, YES terms) and time, for the flattening quote.
    entry_px: i64,
    entry_us: i64,
    /// Others' touch in cents, for a mark at the price we could exit at, not the mid.
    last_bid: f64,
    last_ask: f64,
    conservative: bool,
    /// `floor_strike` from the market payload: the settlement index level this contract compares
    /// against, in index units. On a 15M return-strike market this is the market's OPEN 60-second
    /// index average, so it is already realised and exact — not an estimate. 0.0 where the
    /// payload carried none, which is the only thing that disables `--fair` for a market.
    strike: f64,
    /// The settlement index this market's series resolves on (`BRTI`, `ETHUSD_RTI`, ...), for
    /// joining the `cfbenchmarks_value*` stream to a book. Empty for a non-crypto series.
    index_id: String,
}

impl Mkt {
    fn mtm_c(&self) -> f64 {
        if self.conservative && self.last_bid > 0.0 && self.last_ask > 0.0 {
            // A YES+NO pair is exactly 100; the unpaired rest is marked where it could be sold.
            let pairs = self.yes_ct.min(self.no_ct);
            return self.flow_c + pairs * 100.0 + (self.yes_ct - pairs) * self.last_bid
                + (self.no_ct - pairs) * (100.0 - self.last_ask);
        }
        self.flow_c + self.yes_ct * self.last_mid + self.no_ct * (100.0 - self.last_mid)
    }

    fn mid_at(&self, t: i64) -> Option<f64> {
        self.mids.iter().rev().find(|(vt, _)| *vt <= t).map(|(_, m)| *m)
    }
}

/// Results of REST calls, delivered back to the single-threaded loop.
enum Done {
    Created { coid: String, res: Result<Value> },
    Cancelled { coid: String, res: Result<Value> },
    Amended { coid: String, price: i64, res: Result<Value> },
}

/// Gzipped JSONL. A 3 h, 9-series run wrote 431 MB uncompressed (the box has ~3 GB free);
/// gzip -1 made it 55 MB. `flush` is a gzip SYNC flush, so a crash loses at most one
/// housekeeping interval and the file stays readable up to the last flush.
struct Journal(BufWriter<GzEncoder<File>>, Samples);

impl Journal {
    /// Timed, because this is a synchronous serde + gzip deflate running on the decide loop, and
    /// `j.row("new", ..)` is the last statement before the `tokio::spawn` that POSTs an order.
    /// If `journal_us` turns out to be material, move it to a writer thread the way `probe`
    /// ("gzip must never sit in the receive path") and `shadow`'s Recorder already do.
    fn row(&mut self, kind: &str, v: Value) {
        let t = Instant::now();
        let _ = writeln!(self.0, "{}", json!({"k": kind, "t": unix_us(), "v": v}));
        self.1.push(t.elapsed().as_micros() as i64, 1.0);
    }
    fn flush(&mut self) {
        let _ = self.0.flush();
    }
    fn finish(self) {
        if let Ok(gz) = self.0.into_inner() {
            let _ = gz.finish();
        }
    }
}

/// Stage timings for one WS frame, reset every housekeeping tick.
///
/// Until 2026-10-06 `live` took **no monotonic clock reading per message at all** — a single
/// `unix_us()` wall-clock stamp and nothing after it — so every stage below was invisible and
/// "where does the decision path spend its time" was unanswerable. The order round trip itself is
/// already known (`rtt`/`latency` subcommands, and reconstructible offline from the journal's `new`
/// -> `ack_new` rows); what was missing is everything *before* the send.
///
/// Pure instrumentation: nothing here changes a quoting decision.
struct Timing {
    /// Venue `ts` -> our receipt. The ONLY series here that compares two clocks, so it inherits
    /// the chrony offset (RMS 8.4 us, root delay 330 us on 2026-10-06) plus the venue's own
    /// unknown clock bias. Treat the shape and config-to-config deltas as real, the absolute
    /// level as offset by an unknown constant.
    feed_age_us: Samples,
    /// `serde_json::from_str` on the frame.
    parse_us: Samples,
    /// The two O(markets) scans: `exposure_elsewhere` and `round_other`.
    scan_us: Samples,
    /// Delta handling: `apply_delta` + both `touch()` recomputes. INCLUDES the `own_delta`/`B`
    /// journal rows written inside that block — subtract `journal_us` to split them.
    book_us: Samples,
    /// Receipt -> reaching the `---- decide ----` marker.
    pre_decide_us: Samples,
    /// Receipt -> the `tokio::spawn` that POSTs a new order. The end of everything we control.
    to_spawn_us: Samples,
}

impl Timing {
    fn new() -> Self {
        Self {
            feed_age_us: Samples::new(TIMING_CAP),
            parse_us: Samples::new(TIMING_CAP),
            scan_us: Samples::new(TIMING_CAP),
            book_us: Samples::new(TIMING_CAP),
            pre_decide_us: Samples::new(TIMING_CAP),
            to_spawn_us: Samples::new(TIMING_CAP),
        }
    }

    fn report(&self, journal_us: &Samples) -> Value {
        json!({
            "feed_age_us": self.feed_age_us.summary(&[]),
            "parse_us": self.parse_us.summary(&[]),
            "scan_us": self.scan_us.summary(&[]),
            "book_us": self.book_us.summary(&[]),
            "pre_decide_us": self.pre_decide_us.summary(&[]),
            "to_spawn_us": self.to_spawn_us.summary(&[]),
            "journal_us": journal_us.summary(&[]),
        })
    }

    /// p50 of a series, for the one-line status print.
    fn p50(s: &Samples) -> i64 {
        s.summary(&[])["quantiles"]["p50"].as_i64().unwrap_or(0)
    }
}

pub async fn run(auth: Auth, p: LiveParams) -> Result<String> {
    if p.clip_fp <= 0 || p.clip_fp > p.max_pos_fp {
        bail!("--clip must be > 0 and <= --max-pos (clip {} ct, max-pos {} ct)", p.clip_fp as f64 / SIZE_SCALE as f64, p.max_pos_fp as f64 / SIZE_SCALE as f64);
    }
    if p.book_residual {
        anyhow::ensure!(p.sports.is_none(), "--book-residual is only available in crypto live mode");
        anyhow::ensure!(p.series.len() == 1 && matches!(p.series[0].as_str(), "KXBTC15M" | "KXETH15M"),
            "--book-residual requires one supported series: KXBTC15M or KXETH15M");
        anyhow::ensure!(p.penny_room == 0, "--book-residual cannot be combined with --penny-room");
        anyhow::ensure!(p.spot_bps == 0.0, "--book-residual is the frozen book-only control; do not combine it with --spot-bps");
    }
    // Refuse to start rather than run a spot gate that cannot fire: both of these are otherwise
    // silent, and the engine would quote with a healthy-looking status line and no pull at all.
    // Checked here, before any venue call, because a configuration error should not need a
    // working credential to surface.
    let spot_assets: Vec<&'static str> =
        p.series.iter().filter_map(|s| crate::fastspot::asset_of_series(s)).collect();
    if p.spot_bps > 0.0 {
        anyhow::ensure!(
            !spot_assets.is_empty(),
            "--spot-bps is set but no --series is a crypto 15M series, so the pull would never fire"
        );
        anyhow::ensure!(
            p.spot_venues.len() >= p.spot_min_venues.max(1),
            "--spot-min-venues {} cannot be met by {} venue(s): the pull would be permanently inert",
            p.spot_min_venues,
            p.spot_venues.len()
        );
        anyhow::ensure!(
            p.spot_window_us > 0 && p.spot_max_age_us >= p.spot_window_us,
            "--spot-max-age-ms ({}) must be at least --spot-window-ms ({}), or no venue can hold \
             an observation old enough to span the window and the pull is permanently inert",
            p.spot_max_age_us / 1_000,
            p.spot_window_us / 1_000
        );
    }
    anyhow::ensure!(
        p.pull_amend_ticks > 0 || !p.amend_only,
        "--amend-only with --pull-amend-ticks 0 would amend a pulled quote to its own price, \
         which is a pull that does nothing"
    );
    // Same discipline for model pricing: every way for `--fair` to be silently inert is a
    // start-up failure, because an inert seat and a seat with nothing to quote look identical on
    // the status line. `fairvalue` returns `None` rather than guessing, so a missing input here
    // would mean a run that places no orders and reports no error.
    if p.fair {
        anyhow::ensure!(p.sports.is_none(),
            "--fair is crypto-only: a game has no settlement index or strike to price against, \
             and on sports the width gate is the measured mechanism, not an obstacle");
        anyhow::ensure!(!spot_assets.is_empty(),
            "--fair needs a crypto 15M series; none of {:?} maps to a settlement index", p.series);
        anyhow::ensure!(!p.book_residual,
            "--fair replaces --book-residual: both compute a price, and --book-residual's is \
             anchored on the Kalshi mid, which is the thing --fair exists not to read");
        anyhow::ensure!(p.spot_venues.len() >= p.spot_min_venues.max(1),
            "--spot-min-venues {} cannot be met by {} venue(s): the model could never anchor a \
             level and the seat would quote nothing",
            p.spot_min_venues, p.spot_venues.len());
        anyhow::ensure!(p.fair_reaction_s > 0.0,
            "--fair-reaction-ms must be > 0: it is the measured cost of being stale (11.6 ms \
             index-to-book), and at 0 the margin collapses to --fair-margin-c alone");
        anyhow::ensure!(p.fair_vol_floor > 0.0 && p.fair_vol_ceil >= p.fair_vol_floor,
            "--fair-vol-floor must be > 0 and <= --fair-vol-ceil (got {} and {})",
            p.fair_vol_floor, p.fair_vol_ceil);
        anyhow::ensure!(p.fair_vol_min_samples > 0,
            "--fair-vol-min-samples must be > 0, or an unwarmed vol prices the first quotes");
        // ⛔ Refuse the refuted gross outright. Claiming the paired round trip is what would admit
        // the mid band, and it is measured not to pay: the pair rate reaches 99.14% against a
        // 97.3% break-even and c/pair goes NEGATIVE getting there (0 of 8 series positive in 11
        // cells). A flag that re-opens a closed branch by assertion should not be reachable.
        anyhow::ensure!(p.fair_gross_c <= 0.40,
            "--fair-gross-c {} claims an income this seat is measured NOT to earn: the paired \
             round trip nets -1.27 to +2.25 c/pair by exit policy and is negative per opening in \
             all 11 cells (kalshi-scalp/FINDINGS_pair_rate_20261007.md). The settlement-held gross \
             is 0.25 c; values above 0.40 need a NEW pair-rate measurement, not a flag",
            p.fair_gross_c);
        // The pricer has no opinion inside the settlement window, so a close buffer below it
        // would be a buffer the model never reaches.
        anyhow::ensure!(p.stop_before_close_s as f64 >= crate::fairvalue::SETTLE_AVG_S,
            "--stop-before-close-s {} is inside the {} s settlement average, where the pricer \
             has no opinion; raise it", p.stop_before_close_s, crate::fairvalue::SETTLE_AVG_S);
    }
    fs::create_dir_all(&p.out)?;
    let auth = Arc::new(auth);
    let http = client()?;
    let stamp = unix_us() / 1_000;
    let mut j = Journal(BufWriter::new(GzEncoder::new(
        OpenOptions::new().create(true).append(true).open(p.out.join(format!("live_{stamp}.jsonl.gz")))?,
        Compression::fast(),
    )), Samples::new(TIMING_CAP));
    let mut timing = Timing::new();

    // ---- equity baseline and the cumulative cap across restarts ----
    let equity0 = equity_c(&http, &auth, p.exchange_index).await?;
    let state_path = p.out.join("live_state.json");
    let baseline = match fs::read_to_string(&state_path) {
        Ok(s) => serde_json::from_str::<Value>(&s)?["baseline_equity_c"].as_f64().context("state")?,
        Err(_) => {
            fs::write(&state_path, json!({"baseline_equity_c": equity0, "created_ms": stamp}).to_string())?;
            equity0
        }
    };
    if equity0 < baseline - p.cumulative_max_loss_c {
        bail!("cumulative loss cap hit: equity {equity0:.2}c vs baseline {baseline:.2}c (cap {:.0}c); refusing to start", p.cumulative_max_loss_c);
    }
    eprintln!("ARMED. equity {:.2}c, cumulative baseline {:.2}c, session cap {:.0}c, cumulative cap {:.0}c, round net cap {}",
        equity0, baseline, p.session_max_loss_c, p.cumulative_max_loss_c,
        if p.max_round_net_fp > 0 { format!("{} ct", p.max_round_net_fp as f64 / SIZE_SCALE as f64) } else { "off".into() });
    j.row("start", json!({"equity_c": equity0, "baseline_c": baseline, "series": p.series,
        "book_residual": p.book_residual, "penny_room": p.penny_room, "spot_bps": p.spot_bps,
        "max_round_net_fp": p.max_round_net_fp, "open_lo_c": p.open_lo_c, "open_hi_c": p.open_hi_c}));

    // ---- venue-side kill switch ----
    let group = if dry() { json!({"order_group_id": "dry-run"}) } else { signed(&http, &auth, "POST", "/portfolio/order_groups/create",
        Some(&json!({"contracts_limit": p.group_contracts_per_15s, "exchange_index": p.exchange_index}))).await? };
    let mut group_id = group["order_group_id"].as_str().context("order group id")?.to_owned();
    eprintln!("order group {group_id}: venue cancels all at {} contracts matched / 15 s", p.group_contracts_per_15s);
    j.row("order_group", group.clone());

    let (done_tx, mut done_rx) = mpsc::unbounded_channel::<Done>();
    let (spot_tx, mut spot_rx) = mpsc::channel::<crate::fastspot::Event>(65_536);
    if p.spot_bps > 0.0 {
        eprintln!(
            "spot: {} venues x {} assets, pull at {:.1} bps / {} ms, median of >= {} fresh venues",
            p.spot_venues.len(), spot_assets.len(), p.spot_bps,
            p.spot_window_us / 1_000, p.spot_min_venues
        );
    }
    // `--fair` needs the feed as much as `--spot-bps` does: the model's level is the index
    // carried forward by the spot return, so without a spot socket `ret_bps` is permanently
    // `None` and the seat quotes nothing while looking healthy.
    let (spot_tasks, spot_dropped) = if p.spot_bps > 0.0 || p.fair {
        crate::fastspot::feed(&p.spot_venues, &spot_assets, spot_tx)
    } else {
        (Vec::new(), std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0)))
    };
    let mut spot = crate::fastspot::Consolidated::default();
    // asset -> the series ticker prefix its markets carry, so a pull on "ETH" can find them.
    let asset_series: HashMap<&'static str, String> = p.series.iter()
        .filter_map(|s| crate::fastspot::asset_of_series(s).map(|a| (a, s.clone())))
        .collect();
    let mut spot_pulls = 0u64;
    let mut amends = 0u64;
    // Model pricing state, per settlement index — outside the reconnect loop because the vol
    // EWMA takes `fair_vol_min_samples` seconds to warm and a reconnect must not reset it to
    // "no opinion" and silence the seat.
    let mut index_tick: HashMap<String, crate::fairvalue::IndexTick> = HashMap::new();
    let mut index_vol: HashMap<String, crate::fairvalue::Vol> = HashMap::new();
    // Counters for why a model quote was not produced. A seat that silently stops quoting looks
    // exactly like a seat with nothing to quote, and these separate the two.
    let (mut fair_quotes, mut fair_no_index, mut fair_no_vol, mut fair_no_spot, mut fair_no_strike) =
        (0u64, 0u64, 0u64, 0u64, 0u64);
    let mut fair_one_sided = 0u64;
    // Markets the derived gate refused because the two tolls exceed the gross. On this panel at
    // the measured level precision this is expected to be the large majority of the mid band.
    let mut fair_below_gross = 0u64;
    // Requotes driven by a spot tick rather than a Kalshi frame. This counter is the measurement
    // of whether reading the underlying directly was worth anything: if it stays near zero the
    // seat is still being driven by Kalshi's own (later) clock.
    let mut fair_spot_requotes = 0u64;
    let mut orders: HashMap<String, LiveOrder> = HashMap::new();
    let mut markets: HashMap<String, Mkt> = HashMap::new();
    let mut tokens = 900.0f64;
    let mut last_refill = unix_us();
    let (mut posts, mut cancels, mut rejects, mut fills, mut group_trips) = (0u64, 0u64, 0u64, 0u64, 0u64);
    // Orders of ours that another maker improved past while they rested (counted once each).
    let mut undercuts = 0u64;
    let mut undercut_seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut cash_c = 0.0f64; // from our own fills, for the fast local mark-to-market cap
    // Session P&L the caps use: closed markets' locked-in ledger value + open markets marked.
    let mut realized_c = 0.0f64;
    // Order group tripped: no posting at all until a reset SUCCEEDS (not merely until a timer
    // expires — posting into a tripped group is rejected and must not re-arm the pause).
    let mut group_tripped = false;
    let mut paused_until_us = 0i64;
    let started = Instant::now();
    let deadline = started + Duration::from_secs(p.minutes * 60);
    let mut stop_reason = String::from("deadline");
    let mut game_pause: HashMap<String, i64> = HashMap::new();
    let mut parent_seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let (mut game_pauses, mut jump_pauses) = (0u64, 0u64);
    let mut last_sports_refresh = Instant::now();
    let (mut round_trips, mut rt_pnl_c) = (0u64, 0.0f64);
    // Auto discovery runs off the loop: a full sweep of open sports events takes tens of seconds.
    let (auto_tx, mut auto_rx) = mpsc::channel::<Vec<Value>>(2);
    let auto_task = p.sports.as_ref().and_then(|c| c.auto_series.clone().map(|set| {
        let (http, tx, xi, refresh, max_sp, min_v) = (http.clone(), auto_tx.clone(), p.exchange_index, c.refresh_s, c.max_open_spread_c, c.min_v24);
        tokio::spawn(async move { auto_discover(http, set, xi, refresh, max_sp, min_v, tx).await })
    }));
    let mut auto_latest: Vec<Value> = Vec::new();
    if auto_task.is_some() {
        eprintln!("auto discovery: first sweep...");
        if let Some(v) = auto_rx.recv().await { auto_latest = v; }
    }

    // nohup'd background jobs ignore SIGINT; SIGTERM must also cancel everything on the way out.
    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    'outer: while Instant::now() < deadline {
        // Discover the current open market per series (REST), then subscribe.
        match &p.sports {
            Some(cfg) if cfg.auto_series.is_some() => { apply_sports(auto_latest.iter().map(|m| (m.clone(), false)).collect(), cfg, &mut markets, &mut orders, &mut realized_c, &mut j, true); }
            Some(cfg) => { refresh_sports(&http, &p.series, cfg, p.exchange_index, &mut markets, &mut orders, &mut realized_c, &mut j).await; }
            None => refresh_markets(&http, &p.series, &mut markets).await,
        }
        parent_seen.clear();
        sync_positions(&http, &auth, &mut markets).await?;
        let (mut ws, _) = match crate::connect_ws(signed_ws_request(&auth)?).await {
            Ok(x) => x,
            Err(e) => { eprintln!("ws connect: {e}"); tokio::time::sleep(Duration::from_secs(1)).await; continue; }
        };
        let tickers: Vec<String> = markets.keys().cloned().collect();
        ws.send(Message::Text(json!({"id": 1, "cmd": "subscribe", "params": {
            "channels": ["orderbook_delta", "trade"], "market_tickers": tickers, "use_yes_price": true}}).to_string().into())).await?;
        ws.send(Message::Text(json!({"id": 2, "cmd": "subscribe", "params": {
            "channels": ["fill", "user_orders"]}}).to_string().into())).await?;
        let mut next_id = 3u64;
        if p.fair {
            // One command per index id, deliberately: an id the venue does not know rejects its
            // own command instead of taking the whole subscription down with it.
            let ids: Vec<String> = markets.values()
                .filter(|m| !m.index_id.is_empty())
                .map(|m| m.index_id.clone())
                .collect::<std::collections::HashSet<_>>().into_iter().collect();
            anyhow::ensure!(!ids.is_empty(),
                "--fair needs a crypto 15M series: none of {:?} maps to a settlement index", p.series);
            for id in &ids {
                ws.send(Message::Text(json!({"id": next_id, "cmd": "subscribe", "params": {
                    "channels": ["cfbenchmarks_value", "cfbenchmarks_value_5hz"],
                    "index_ids": [id]}}).to_string().into())).await?;
                next_id += 1;
            }
            eprintln!("fair: model pricing on, indices {ids:?}");
        }
        let mut seqs: HashMap<u64, u64> = HashMap::new();
        for m in markets.values_mut() { m.book = None; }
        let mut housekeeping = interval(Duration::from_secs(10));
        let mut equity_tick = interval(Duration::from_secs(20));
        equity_tick.tick().await;

        loop {
            let msg = tokio::select! {
                m = ws.next() => m,
                d = done_rx.recv() => {
                    let Some(d) = d else { continue };
                    match d {
                        Done::Created { coid, res } => match res {
                            Ok(v) => {
                                j.row("ack_new", json!({"coid": coid, "resp": v}));
                                if let Some(o) = orders.get_mut(&coid) {
                                    o.order_id = v["order_id"].as_str().map(str::to_owned);
                                    if o.st == St::PendingNew { o.st = St::Resting; }
                                    // A cancel requested before the ack could not name the order.
                                    if o.st == St::PendingCancel { if let Some(id) = o.order_id.clone() {
                                        spawn_cancel(&http, &auth, &done_tx, coid.clone(), id, o.ticker.clone(), p.exchange_index);
                                    }}
                                }
                            }
                            Err(e) => {
                                rejects += 1;
                                let es = format!("{e:#}");
                                j.row("reject_new", json!({"coid": coid, "err": es}));
                                if es.contains("order_group") && !group_tripped {
                                    group_trips += 1;
                                    group_tripped = true;
                                    paused_until_us = unix_us() + 20_000_000;
                                }
                                release(&mut orders, &mut markets, &coid);
                            }
                        },
                        Done::Amended { coid, price, res } => {
                            match res {
                                Ok(v) => {
                                    j.row("ack_amend", json!({"coid": coid, "price": price, "resp": v}));
                                    if let Some(o) = orders.get_mut(&coid) {
                                        if o.st == St::PendingAmend { o.st = St::Resting; o.price = price; }
                                    }
                                }
                                Err(e) => {
                                    rejects += 1;
                                    let es = format!("{e:#}");
                                    j.row("reject_amend", json!({"coid": coid, "err": es}));
                                    if es.contains("404") || es.contains("not_found") {
                                        release(&mut orders, &mut markets, &coid); // filled or gone
                                    } else if let Some(o) = orders.get_mut(&coid) {
                                        if o.st == St::PendingAmend { o.st = St::Resting; } // still at the old price
                                    }
                                }
                            }
                        }
                        Done::Cancelled { coid, res } => {
                            match &res {
                                Ok(v) => j.row("ack_cancel", json!({"coid": coid, "resp": v})),
                                Err(e) => { j.row("reject_cancel", json!({"coid": coid, "err": format!("{e:#}")})); }
                            }
                            // Either way the order is gone or already terminal (filled/cancelled);
                            // user_orders is the authority and will also release it.
                            release(&mut orders, &mut markets, &coid);
                        }
                    }
                    continue;
                }
                v = auto_rx.recv(), if auto_task.is_some() => {
                    let Some(v) = v else { continue };
                    if let Some(cfg) = &p.sports {
                        let new = apply_sports(v.into_iter().map(|m| (m, false)).collect(), cfg, &mut markets, &mut orders, &mut realized_c, &mut j, true);
                        if !new.is_empty() {
                            ws.send(Message::Text(json!({"id": next_id, "cmd": "subscribe", "params": {
                                "channels": ["orderbook_delta", "trade"], "market_tickers": new, "use_yes_price": true}}).to_string().into())).await?;
                            next_id += 1;
                        }
                    }
                    continue;
                }
                _ = housekeeping.tick() => {
                    j.flush();
                    // Stage timings for the interval just ended, then reset so the next report
                    // describes the next interval rather than the first frames of the run.
                    let stages = timing.report(&j.1);
                    eprintln!("        stage us p50: feed_age={} parse={} scan={} book={} pre_decide={} to_spawn={} journal={}",
                        Timing::p50(&timing.feed_age_us), Timing::p50(&timing.parse_us),
                        Timing::p50(&timing.scan_us), Timing::p50(&timing.book_us),
                        Timing::p50(&timing.pre_decide_us), Timing::p50(&timing.to_spawn_us),
                        Timing::p50(&j.1));
                    j.row("timing", stages);
                    timing = Timing::new();
                    j.1 = Samples::new(TIMING_CAP);
                    let now_ms = unix_us() / 1_000;
                    // Pull resting orders in markets inside the close buffer; drop closed markets.
                    // A REDUCING order gets its own, tighter buffer: pulling the only thing that
                    // can close a position is what turns a 1-ct wing fill into a settlement loss.
                    let late: Vec<String> = orders.values()
                        .filter(|o| markets.get(&o.ticker).is_some_and(|m| {
                            let reducing = (o.side == ASK && m.pos_fp > 0) || (o.side == BID && m.pos_fp < 0);
                            let buffer = if reducing { p.exit_stop_before_close_s } else { p.stop_before_close_s };
                            m.close_unix_ms - now_ms < buffer * 1_000
                        }))
                        .map(|o| o.coid.clone()).collect();
                    for c in late { request_cancel(&http, &auth, &done_tx, &mut orders, &c, p.exchange_index, &mut cancels); }
                    let before = markets.len();
                    // Lock in the ledger value of markets we are about to forget.
                    for m in markets.values().filter(|m| now_ms >= m.close_unix_ms + 5_000) {
                        realized_c += m.mtm_c();
                    }
                    markets.retain(|_, m| now_ms < m.close_unix_ms + 5_000);
                    if let Some(cfg) = &p.sports {
                        if cfg.auto_series.is_none() && last_sports_refresh.elapsed().as_secs() >= cfg.refresh_s {
                            last_sports_refresh = Instant::now();
                            let new = refresh_sports(&http, &p.series, cfg, p.exchange_index, &mut markets, &mut orders, &mut realized_c, &mut j).await;
                            if !new.is_empty() {
                                ws.send(Message::Text(json!({"id": next_id, "cmd": "subscribe", "params": {
                                    "channels": ["orderbook_delta", "trade"], "market_tickers": new, "use_yes_price": true}}).to_string().into())).await?;
                                next_id += 1;
                            }
                        }
                    } else if markets.len() != before || markets.len() < p.series.len() {
                        let old: Vec<String> = markets.keys().cloned().collect();
                        refresh_markets(&http, &p.series, &mut markets).await;
                        if let Err(e) = sync_positions(&http, &auth, &mut markets).await { eprintln!("positions: {e:#}"); }
                        let new: Vec<String> = markets.keys().filter(|k| !old.contains(k)).cloned().collect();
                        if !new.is_empty() {
                            ws.send(Message::Text(json!({"id": next_id, "cmd": "subscribe", "params": {
                                "channels": ["orderbook_delta", "trade"], "market_tickers": new, "use_yes_price": true}}).to_string().into())).await?;
                            next_id += 1;
                            j.row("markets", json!(markets.keys().collect::<Vec<_>>()));
                        }
                    }
                    if group_tripped && unix_us() > paused_until_us {
                        let path = format!("/portfolio/order_groups/{group_id}/reset");
                        match signed(&http, &auth, "PUT", &path, Some(&json!({}))).await {
                            Ok(_) => { j.row("group_reset", json!({})); group_tripped = false; paused_until_us = 0; }
                            // A tripped group is DELETED by the venue (reset → 404
                            // order_group_not_found, 2026-09-23), so replace it instead.
                            Err(e) if format!("{e:#}").contains("order_group_not_found") => {
                                let body = json!({"contracts_limit": p.group_contracts_per_15s, "exchange_index": p.exchange_index});
                                match signed(&http, &auth, "POST", "/portfolio/order_groups/create", Some(&body)).await {
                                    Ok(g) => {
                                        if let Some(id) = g["order_group_id"].as_str() {
                                            group_id = id.to_owned();
                                            group_tripped = false;
                                            paused_until_us = 0;
                                            j.row("group_replaced", g.clone());
                                            eprintln!("order group replaced: {group_id}");
                                        }
                                    }
                                    Err(e) => eprintln!("order group re-create failed: {e:#}"),
                                }
                            }
                            Err(e) => eprintln!("group reset failed: {e:#}"),
                        }
                    }
                    if Instant::now() >= deadline { break 'outer; }
                    continue;
                }
                sp = spot_rx.recv(), if p.spot_bps > 0.0 || p.fair => {
                    let Some(ev) = sp else { continue };
                    // A venue dropping out silently shrinks the median the pull votes on, so the
                    // lifecycle note is journalled even though the quotes are not: one row per
                    // quote across 10 venues would be ~100x the journal for no forensic gain.
                    if let crate::fastspot::Event::Note(v, what, detail) = &ev {
                        eprintln!("spot {} {what}: {detail}", v.name());
                        j.row("spot_note", json!({"venue": v.name(), "what": what, "detail": detail}));
                    }
                    spot.apply(&ev);
                    let crate::fastspot::Event::Q(q) = ev else { continue };
                    // ⚑ Reprice on the SPOT tick, which is the fastest clock we have. Waiting
                    // for the next Kalshi frame would price off a feed that reaches us 5.7 ms
                    // after the venue stamps it and 26-73 ms after CF computes the index
                    // (`FINDINGS_altfeed_index_path_20261007.md`) — i.e. it would throw away the
                    // whole reason this seat can quote continuously. At the measured 2.83 c/bp a
                    // few bps of unmodelled spot move is more than the entire margin.
                    if p.fair {
                        if let Some(series) = asset_series.get(q.asset) {
                            let prefix = format!("{series}-");
                            let tick = index_tick.get(&mk_index_id(series)).copied();
                            let sigma = index_vol.get(&mk_index_id(series)).and_then(|v| v.sigma(
                                p.fair_vol_min_samples,
                                crate::fairvalue::annual_to_per_sqrt_s(p.fair_vol_floor),
                                crate::fairvalue::annual_to_per_sqrt_s(p.fair_vol_ceil),
                            ));
                            // `moves`: (coid, new price). Collected before any mutation so the
                            // borrow of `markets` ends before `request_amend` takes `orders`.
                            let mut moves: Vec<(String, i64)> = Vec::new();
                            if let (Some(tk), Some(sigma)) = (tick, sigma) {
                                if q.recv_us - tk.recv_us <= p.fair_max_index_age_us {
                                    let ret = spot.ret_bps(q.asset, q.recv_us,
                                        (q.recv_us - tk.source_us).max(1),
                                        p.spot_max_age_us, p.spot_min_venues);
                                    for (t, m) in markets.iter().filter(|(t, _)| t.starts_with(&prefix)) {
                                        let tau_s = (m.close_unix_ms - q.recv_us / 1_000) as f64 / 1_000.0;
                                        if m.strike <= 0.0 || m.last_bid <= 0.0 || m.last_ask <= 0.0 { continue }
                                        let Some(level) = ret.and_then(|r| crate::fairvalue::anchored_level(&tk, r)) else { continue };
                                        let Some(fv) = crate::fairvalue::digital_cents(level, m.strike, tau_s, sigma) else { continue };
                                        let Some((ok, need)) = crate::fairvalue::quotable(
                                            level, m.strike, tau_s, sigma, p.fair_reaction_s,
                                            p.fair_level_precision_bp, p.fair_gross_c) else { continue };
                                        if !ok { continue }
                                        let (b, a) = ((m.last_bid * 100.0).round() as i64, (m.last_ask * 100.0).round() as i64);
                                        let want = crate::fairvalue::pair(fv, m.pos_fp,
                                            need + p.fair_margin_c, p.fair_skew_c, b, a, &m.ranges);
                                        for (idx, px) in [(BID, want.bid), (ASK, want.ask)] {
                                            let Some(c) = m.slots[idx].clone() else { continue };
                                            let Some(o) = orders.get(&c) else { continue };
                                            if o.st != St::Resting || o.remaining_fp != p.clip_fp || o.order_id.is_none() { continue }
                                            // No price for this side any more: the model now
                                            // refuses it. An amend cannot say "no quote", so
                                            // leave it to the Kalshi-frame path to cancel.
                                            let Some(px) = px else { continue };
                                            if ((o.price - px).abs() as f64) < p.fair_requote_c * 100.0 { continue }
                                            moves.push((c, px));
                                        }
                                        let _ = t;
                                    }
                                }
                            }
                            for (c, px) in moves {
                                if tokens < 10.0 { break }
                                if request_amend(&http, &auth, &done_tx, &mut orders, &c, px, p.clip_fp, &mut amends) {
                                    tokens -= 10.0;
                                    fair_spot_requotes += 1;
                                }
                            }
                        }
                    }
                    // Under `--fair` the spot tick has already been used as a price. The
                    // threshold pull below is the gate `--fair` replaces, and running both would
                    // gate the seat twice on one signal.
                    if p.spot_bps <= 0.0 { continue; }
                    let Some(r) = spot.ret_bps(
                        q.asset, q.recv_us, p.spot_window_us, p.spot_max_age_us, p.spot_min_venues,
                    ) else { continue };
                    if r.abs() <= p.spot_bps { continue; }
                    // Spot up = YES worth more: our ask is stale. Act on the spot tick itself,
                    // not at the next Kalshi message — the Kalshi book is what lags.
                    let side = if r > 0.0 { ASK } else { BID };
                    let Some(series) = asset_series.get(q.asset) else { continue };
                    let prefix = format!("{series}-");
                    // `(coid, where it parks)`. The park price is read here, while the market's
                    // grid is in hand, because the wings step 0.1c and the middle 1c — and it is
                    // measured from the OTHERS' touch, never from our own price: measuring it
                    // from our own price would walk the quote another N ticks on every tick of a
                    // sustained move, at 10 tokens each, and never reach a stable place.
                    let victims: Vec<(String, i64)> = markets.iter()
                        .filter(|(t, _)| t.starts_with(&prefix))
                        .filter_map(|(_, m)| {
                            let c = m.slots[side].clone()?;
                            let o = orders.get(&c)?;
                            if o.st == St::PendingCancel { return None }
                            let touch_c = if side == BID { m.last_bid } else { m.last_ask };
                            let touch = (touch_c * 100.0).round() as i64;
                            // No touch seen in this market yet: an amend has nothing to aim at,
                            // and 0 makes `request_amend` refuse so the cancel below still pulls.
                            if touch <= 0 { return Some((c, 0)) }
                            let park = pull_price(&m.ranges, side, touch, p.pull_amend_ticks);
                            let clear = if side == BID { o.price <= park } else { o.price >= park };
                            if clear { return None }
                            Some((c, park))
                        })
                        .collect();
                    if victims.is_empty() { continue; }
                    let venues = spot.venue_rets_bps(q.asset, q.recv_us, p.spot_window_us, p.spot_max_age_us);
                    for (c, park) in victims {
                        // An amend reaches the book in 2.6-2.9 ms against a cancel's 4.79, and
                        // leaves the quote alive N ticks out of the way rather than gone. Where
                        // the amend cannot express it — a partial fill, no id yet, a price off the
                        // grid, or not enough tokens for the dearer verb — cancel. The pull
                        // always happens; only the verb is negotiable.
                        let how = if p.amend_only && tokens >= 10.0
                            && request_amend(&http, &auth, &done_tx, &mut orders, &c, park, p.clip_fp, &mut amends)
                        {
                            tokens -= 10.0;
                            "amend"
                        } else {
                            if tokens < 2.0 { break; }
                            tokens -= 2.0;
                            request_cancel(&http, &auth, &done_tx, &mut orders, &c, p.exchange_index, &mut cancels);
                            "cancel"
                        };
                        spot_pulls += 1;
                        j.row("spot_pull", json!({"coid": c, "r_bps": r, "asset": q.asset,
                            "how": how, "park": park,
                            "venues": venues.iter().map(|(v, x)| json!([v.name(), x])).collect::<Vec<_>>()}));
                    }
                    continue;
                }
                _ = equity_tick.tick() => {
                    // Caps run on OUR ledger, marked to mid (a pair is exactly 100): the venue's
                    // position fields net pairs away, which read as a phantom −$6 on 2026-09-25.
                    let session = realized_c + markets.values().map(Mkt::mtm_c).sum::<f64>();
                    let cumulative = equity0 - baseline + session;
                    let venue = equity_c(&http, &auth, p.exchange_index).await.ok();
                    j.row("equity", json!({"session_mtm_c": session, "cumulative_c": cumulative, "venue_cash_plus_net_exposure_c": venue}));
                    eprintln!("[{}s] session {:+.2}c cumulative {:+.2}c (venue cash+net exposure {:.2}c) | posts={} cancels={} amends={} spot_pulls={} rejects={} fills={} group_trips={} undercuts={} resting={}",
                        started.elapsed().as_secs(), session, cumulative, venue.unwrap_or(f64::NAN), posts, cancels, amends, spot_pulls, rejects, fills, group_trips, undercuts, orders.len());
                    if p.fair {
                        // Why the model did or did not price, separated: a seat that has stopped
                        // quoting and a seat with nothing to quote look identical without this.
                        let vols: Vec<String> = index_vol.iter().map(|(id, v)| {
                            let s = v.sigma(p.fair_vol_min_samples,
                                crate::fairvalue::annual_to_per_sqrt_s(p.fair_vol_floor),
                                crate::fairvalue::annual_to_per_sqrt_s(p.fair_vol_ceil));
                            match s {
                                Some(s) => format!("{id}={:.0}%/{}", 100.0 * s * (365.0 * 86_400.0f64).sqrt(), v.samples()),
                                None => format!("{id}=warming/{}", v.samples()),
                            }
                        }).collect();
                        eprintln!("        fair: priced={fair_quotes} one_sided={fair_one_sided} spot_requotes={fair_spot_requotes} \
                                   | skipped: below_gross={fair_below_gross} no_index={fair_no_index} no_vol={fair_no_vol} no_spot={fair_no_spot} no_strike={fair_no_strike} \
                                   | vol {}", vols.join(" "));
                        j.row("fair_stats", json!({"priced": fair_quotes, "one_sided": fair_one_sided,
                            "spot_requotes": fair_spot_requotes, "below_gross": fair_below_gross, "no_index": fair_no_index,
                            "no_vol": fair_no_vol, "no_spot": fair_no_spot, "no_strike": fair_no_strike,
                            "vol": vols}));
                    }
                    if p.spot_bps > 0.0 {
                        // The pull votes a median over FRESH venues, so the count of fresh venues
                        // per asset is the gate's real state: fall below --spot-min-venues and
                        // the pull is inert while every other line still looks healthy.
                        let now = unix_us();
                        let health: Vec<String> = spot_assets.iter()
                            .map(|a| format!("{a}:{}", spot.fresh_venues(a, now, p.spot_max_age_us)))
                            .collect();
                        // The level we believed, per asset, alongside the venue count that
                        // produced it: without it a pull row says the move but not the price it
                        // was a move from.
                        let mids: serde_json::Map<String, Value> = spot_assets.iter()
                            .filter_map(|a| spot.usd_mid(a, now, p.spot_max_age_us).map(|m| ((*a).to_owned(), json!(m))))
                            .collect();
                        let lost = spot_dropped.load(std::sync::atomic::Ordering::Relaxed);
                        eprintln!("        spot: fresh venues {} (min {}) | dropped {lost} | notes {}",
                            health.join(" "), p.spot_min_venues, spot.notes);
                        j.row("spot_health", json!({"fresh": health, "usd_mid": mids,
                            "dropped": lost, "notes": spot.notes}));
                    }
                    if p.sports.is_some() {
                        let open_pos: f64 = markets.values().map(|m| m.pos_fp.abs() as f64).sum::<f64>() / SIZE_SCALE as f64;
                        eprintln!("        sports: markets={} quotable={} games_with_parent_seen={} game_pauses={} jump_pauses={} open_abs_pos_ct={:.0} round_trips={} rt_pnl={:+.1}c",
                            markets.len(), markets.values().filter(|m| !m.parent).count(), parent_seen.len(), game_pauses, jump_pauses, open_pos, round_trips, rt_pnl_c);
                    }
                    if session < -p.session_max_loss_c { stop_reason = format!("session loss cap: {session:.2}c"); break 'outer; }
                    if cumulative < -p.cumulative_max_loss_c { stop_reason = format!("cumulative loss cap: {cumulative:.2}c"); break 'outer; }
                    continue;
                }
                _ = tokio::signal::ctrl_c() => { stop_reason = "ctrl-c".into(); break 'outer; }
                _ = sigterm.recv() => { stop_reason = "sigterm".into(); break 'outer; }
            };
            let text = match msg {
                Some(Ok(Message::Text(t))) => t,
                Some(Ok(Message::Ping(x))) => { ws.send(Message::Pong(x)).await?; continue; }
                Some(Ok(_)) => continue,
                Some(Err(e)) => { eprintln!("ws error: {e}"); break; }
                None => { eprintln!("ws closed"); break; }
            };
            let now = unix_us();
            let t_recv = Instant::now();
            let t_parse = Instant::now();
            let v: Value = match serde_json::from_str(&text) { Ok(v) => v, Err(_) => continue };
            timing.parse_us.push(t_parse.elapsed().as_micros() as i64, 1.0);
            if let (Some(sid), Some(seq)) = (v["sid"].as_u64(), v["seq"].as_u64()) {
                if let Some(prev) = seqs.insert(sid, seq) {
                    if seq != prev + 1 { eprintln!("seq gap sid {sid}"); break; }
                }
            }
            let kind = v["type"].as_str().unwrap_or("");
            let m = &v["msg"];
            // The settlement index carries no `market_ticker`, so it is handled before any of
            // the per-market arms. Both channels are subscribed and they carry DIFFERENT shapes
            // (`FINDINGS_altfeed_index_path_20261007.md`): the 5 Hz one has `value_usd` and
            // `source_ts_ms` at the top level, the 1 Hz one nests CF's payload in a `data`
            // JSON **string** and has no `source_ts_ms` — its stamp is `data.time`. A reader
            // that knows only one silently drops the other.
            if kind.starts_with("cfbenchmarks") {
                if let Some(id) = m["index_id"].as_str() {
                    let data: Option<Value> = m["data"].as_str().and_then(|s| serde_json::from_str(s).ok());
                    let value = m["value_usd"].as_str().and_then(|s| s.parse::<f64>().ok())
                        .or_else(|| data.as_ref()?["value"].as_str()?.parse::<f64>().ok());
                    // CF's own stamp, never our receipt: the spot increment is measured from the
                    // instant CF computed the level, and we are 26-73 ms downstream of it.
                    let source_ms = m["source_ts_ms"].as_i64()
                        .or_else(|| data.as_ref().and_then(|d| d["time"].as_str().and_then(rfc3339_us)).map(|us| us / 1_000));
                    if let (Some(value), Some(ms)) = (value, source_ms) {
                        let tick = crate::fairvalue::IndexTick {
                            value, source_us: ms * 1_000, recv_us: now,
                        };
                        index_vol.entry(id.to_owned())
                            .or_insert_with(|| crate::fairvalue::Vol::new(
                                1_000_000, p.fair_vol_half_life))
                            .push(tick.source_us, value);
                        index_tick.insert(id.to_owned(), tick);
                    }
                }
                continue;
            }
            match kind {
                "fill" => {
                    fills += 1;
                    j.row("fill", m.clone());
                    let ticker = m["market_ticker"].as_str().unwrap_or("").to_owned();
                    let px = parse_value(m.get("yes_price_dollars"), PRICE_SCALE).unwrap_or(0) as f64 / 100.0;
                    let ct = parse_value(m.get("count_fp"), SIZE_SCALE).unwrap_or(0) as f64 / SIZE_SCALE as f64;
                    let buy_yes = m["book_side"].as_str() == Some("bid");
                    cash_c += if buy_yes { -px * ct } else { px * ct };
                    if let Some(mk) = markets.get_mut(&ticker) {
                        // Bid fill = bought YES at px; ask fill = sold YES at px = bought NO at 100 - px.
                        if buy_yes { mk.yes_ct += ct; mk.flow_c -= px * ct; }
                        else { mk.no_ct += ct; mk.flow_c -= (100.0 - px) * ct; }
                        if let Ok(post) = parse_value(m.get("post_position_fp"), SIZE_SCALE) {
                            let was = mk.pos_fp;
                            mk.pos_fp = post; // the venue's number, not ours
                            if post == 0 && was != 0 && mk.entry_px > 0 {
                                let exit = (px * 100.0).round() as i64;
                                let pnl = if was > 0 { exit - mk.entry_px } else { mk.entry_px - exit } as f64 / 100.0;
                                round_trips += 1;
                                rt_pnl_c += pnl;
                                j.row("round_trip", json!({"ticker": ticker, "entry": mk.entry_px, "exit": exit, "long": was > 0, "pnl_c": pnl, "held_s": (now - mk.entry_us) / 1_000_000}));
                            }
                            if post == 0 { mk.entry_px = 0; mk.entry_us = 0; }
                            else if was == 0 || was.signum() != post.signum() {
                                mk.entry_px = (px * 100.0).round() as i64;
                                mk.entry_us = now;
                            }
                            // The position authority has landed: release the post hold. Inside the
                            // `Ok(post)` arm on purpose — if the position did not parse, nothing
                            // authoritative arrived and the backstop timer must stand.
                            observe_fill(mk);
                        }
                    }
                    continue;
                }
                "user_order" => {
                    j.row("user_order", m.clone());
                    let coid = m["client_order_id"].as_str().unwrap_or("").to_owned();
                    let st = m["status"].as_str().unwrap_or("");
                    if let (Some(o), Ok(rem)) = (orders.get_mut(&coid), parse_value(m.get("remaining_count_fp"), SIZE_SCALE)) {
                        o.remaining_fp = rem;
                    }
                    if m["fill_count_fp"].as_str().is_some_and(|f| f != "0.00") {
                        let ticker = orders.get(&coid).map(|o| o.ticker.clone())
                            .or_else(|| m["ticker"].as_str().map(str::to_owned));
                        if let Some(mk) = ticker.and_then(|t| markets.get_mut(&t)) {
                            announce_fill(mk, now, p.fill_hold_us);
                        }
                    }
                    if st == "canceled" || st == "executed" {
                        if st == "canceled" && orders.contains_key(&coid) && m["remaining_count_fp"].as_str() == Some("0.00") {
                            // Possibly the order group firing: venue cancelled us, not we.
                            if orders.get(&coid).is_some_and(|o| o.st != St::PendingCancel) {
                                j.row("venue_cancel", json!({"coid": coid}));
                                if !group_tripped {
                                    group_trips += 1;
                                    group_tripped = true;
                                    paused_until_us = now + 20_000_000;
                                }
                            }
                        }
                        release(&mut orders, &mut markets, &coid);
                    }
                    continue;
                }
                _ => {}
            }
            let Some(ticker) = m["market_ticker"].as_str() else { continue };
            let t_scan = Instant::now();
            // Sports caps: worst-case contracts in every OTHER quoted market (this game, all games).
            let exposure_elsewhere = p.sports.as_ref().map(|_| {
                let game = ticker.split('-').nth(1).unwrap_or("");
                let (mut g, mut t) = (0i64, 0i64);
                for (tk, om) in markets.iter().filter(|(tk, om)| !om.parent && tk.as_str() != ticker) {
                    let _ = tk;
                    let rest = |k: usize| om.slots[k].as_ref().and_then(|c| orders.get(c)).map_or(0, |o| o.remaining_fp);
                    let pot = (om.pos_fp + rest(BID)).abs().max((om.pos_fp - rest(ASK)).abs());
                    t += pot;
                    if om.game == game { g += pot; }
                }
                (g, t)
            });
            // Round cap: the summed position of every OTHER market closing with this one.
            let round_other = (p.max_round_net_fp > 0).then(|| {
                let close = markets.get(ticker).map_or(-1, |m| m.close_unix_ms);
                markets.iter().filter(|(tk, om)| tk.as_str() != ticker && om.close_unix_ms == close)
                    .map(|(_, om)| om.pos_fp).sum::<i64>()
            });
            timing.scan_us.push(t_scan.elapsed().as_micros() as i64, 1.0);
            let Some(mk) = markets.get_mut(ticker) else { continue };
            let vt = match kind {
                "orderbook_snapshot" => {
                    mk.book = Book::from_snapshot(m).ok();
                    if mk.parent && mk.book.is_some() { parent_seen.insert(mk.game.clone()); }
                    if let Some(t) = mk.book.as_ref().map(Book::touch) {
                        if let (Some(b), Some(a)) = (t.yes_bid_fp, t.yes_ask_fp) {
                            mk.last_mid = (b + a) as f64 / 200.0;
                            if p.book_residual { mk.receipt_mids.push_back((now, mk.last_mid)); }
                        }
                    }
                    continue;
                }
                "orderbook_delta" => m["ts"].as_str().and_then(rfc3339_us),
                "trade" => m["ts_ms"].as_i64().map(|x| x * 1_000),
                _ => None,
            };
            let Some(vt) = vt else { continue };
            timing.feed_age_us.push(now - vt, 1.0);
            let Some(book) = mk.book.as_mut() else { continue };
            let t_book = Instant::now();
            if kind == "orderbook_delta" {
                let previous_touch = book.touch();
                let size_before = book.size_at(m["side"].as_str().unwrap_or(""),
                    parse_value(m.get("price_dollars"), PRICE_SCALE).unwrap_or(-1));
                if let Err(e) = book.apply_delta(m) { eprintln!("delta {ticker}: {e:#}"); break; }
                // Our own order joining the book: log exact join time and queue ahead of us.
                if let Some(coid) = m["client_order_id"].as_str() {
                    j.row("own_delta", json!({"coid": coid, "vt": vt, "recv": now, "delta": m["delta_fp"],
                        "price": m["price_dollars"], "side": m["side"], "level_before": size_before}));
                }
                let t = book.touch();
                if let (Some(b), Some(a)) = (t.yes_bid_fp, t.yes_ask_fp) {
                    let mid = (b + a) as f64 / 200.0;
                    if mk.mids.back().is_none_or(|(_, x)| *x != mid) {
                        mk.mids.push_back((vt, mid));
                        mk.last_mid = mid;
                    }
                    // Preserve size-only changes for queue and capacity diagnostics.
                    if t != previous_touch {
                        j.row("B", json!([ticker, vt, b, t.yes_bid_size_fp, a, t.yes_ask_size_fp]));
                    }
                    // Parents keep the whole move window; quoted books only need 2 s.
                    let keep = p.sports.as_ref().filter(|_| mk.parent).map_or(2_000_000, |c| c.parent_window_us + 2_000_000);
                    while mk.mids.len() > 2 && mk.mids[1].0 < vt - keep { mk.mids.pop_front(); }
                }
            } else {
                j.row("T", json!([ticker, vt, m["taker_side"], m["yes_price_dollars"], m["count_fp"]]));
            }
            timing.book_us.push(t_book.elapsed().as_micros() as i64, 1.0);

            // ---- sports: a parent book is a score feed, never quoted ----
            if let Some(cfg) = &p.sports {
                if mk.parent {
                    if kind != "orderbook_delta" { continue; }
                    let (Some(m1), Some(m0)) = (mk.mids.back().map(|x| x.1), mk.mid_at(vt - cfg.parent_window_us)) else { continue };
                    // A book pinned in a tail (both ends < 3c or > 97c) is a decided line, not news.
                    if (m1 - m0).abs() < cfg.parent_move_c || m1.max(m0) < 3.0 || m1.min(m0) > 97.0 { continue; }
                    let game = mk.game.clone();
                    if game_pause.get(&game).is_some_and(|u| *u > now) { game_pause.insert(game, now + cfg.pause_us); continue; }
                    game_pauses += 1;
                    j.row("parent_pause", json!({"game": game, "parent": ticker, "from": m0, "to": m1, "vt": vt}));
                    pause_game(&game, now + cfg.pause_us, &mut game_pause, &markets, &mut orders, &http, &auth, &done_tx, p.exchange_index, &mut cancels);
                    continue;
                }
            }
            // ---- decide ----
            timing.pre_decide_us.push(t_recv.elapsed().as_micros() as i64, 1.0);
            if group_tripped || now < mk.hold_until_us { continue; }
            tokens = (tokens + (now - last_refill) as f64 / 1e6 * 300.0).min(900.0);
            last_refill = now;
            // The market as OTHER participants make it: our own resting clip removed, or an
            // improved quote of ours would read as the touch and we would penny ourselves.
            let own = |i: usize| mk.slots[i].as_ref().and_then(|c| orders.get(c));
            let (own_bid, own_ask) = (own(BID).map(|o| o.price), own(ASK).map(|o| o.price));
            let own_sz = |i: usize| own(i).map_or(0, |o| o.remaining_fp);
            let (Some((bid, bsz)), Some((ask, asz))) = (
                book.best_excluding("yes", own_bid, own_sz(BID)),
                book.best_excluding("no", own_ask, own_sz(ASK)),
            ) else { continue };
            let mid = (bid + ask) as f64 / 200.0;
            mk.last_bid = bid as f64 / 100.0;
            mk.last_ask = ask as f64 / 100.0;
            if p.book_residual {
                if mk.receipt_mids.back().is_none_or(|(_, old)| *old != mid) {
                    mk.receipt_mids.push_back((now, mid));
                }
                while mk.receipt_mids.len() > 2 && mk.receipt_mids[1].0 < now - 2_000_000 {
                    mk.receipt_mids.pop_front();
                }
            }
            let anchor = p.book_residual.then(|| mk.receipt_mids.iter().rev()
                .find(|(seen, _)| *seen <= now - 1_000_000).map(|(_, value)| *value)).flatten();
            let mom = if p.book_residual { anchor.map(|m0| mid - m0).unwrap_or(0.0) }
                else { mk.mid_at(vt - 1_000_000).map(|m0| mid - m0).unwrap_or(0.0) };
            let tot = (bsz + asz).max(1) as f64;
            let open_ok = mk.close_unix_ms - now / 1_000 > p.stop_before_close_s * 1_000;
            let postable = mid >= p.mid_lo_c && mid <= p.mid_hi_c && open_ok;
            // Sports: a jump in this book pauses its whole game (a score moves every ladder).
            let mut jump_game: Option<String> = None;
            let mut sports_ok = true;
            let mut sports_open = true;
            if let Some(cfg) = &p.sports {
                if let Some(m0) = mk.mid_at(vt - 2_000_000) {
                    if (mid - m0).abs() >= cfg.jump_c && !game_pause.get(&mk.game).is_some_and(|u| *u > now) {
                        jump_game = Some(mk.game.clone());
                    }
                }
                sports_ok = jump_game.is_none()
                    && (cfg.parents.is_empty() || parent_seen.contains(&mk.game))
                    && !game_pause.get(&mk.game).is_some_and(|u| *u > now);
                sports_open = sports_ok && ask - bid >= cfg.min_spread_c * 100
                    && ask - bid <= cfg.max_open_spread_c * 100
                    && mid >= p.mid_lo_c && mid <= p.mid_hi_c;
            }
            // ⚑ MODEL PRICE. Computed once per market per frame, from the settlement index, the
            // strike, the time to close and realised vol. Nothing in it reads this book — that
            // is the whole point (see `LiveParams::fair`). `None` anywhere means no model quote:
            // the seat goes quiet rather than falling back to the mid, because a fallback to the
            // mid is what makes a two-sided quote blind to a mispricing.
            let fair: Option<crate::fairvalue::Pair> = p.fair.then(|| {
                if mk.strike <= 0.0 || mk.index_id.is_empty() { fair_no_strike += 1; return None; }
                let Some(tick) = index_tick.get(&mk.index_id) else { fair_no_index += 1; return None };
                if now - tick.recv_us > p.fair_max_index_age_us { fair_no_index += 1; return None; }
                let Some(sigma) = index_vol.get(&mk.index_id).and_then(|v| v.sigma(
                    p.fair_vol_min_samples,
                    crate::fairvalue::annual_to_per_sqrt_s(p.fair_vol_floor),
                    crate::fairvalue::annual_to_per_sqrt_s(p.fair_vol_ceil),
                )) else { fair_no_vol += 1; return None };
                // The level: exact, in the strike's units, from the index — carried forward by
                // the median per-venue spot return over exactly [CF's stamp, now]. Per-venue
                // returns then median is what makes this immune to the USDT/perp basis, and a
                // basis here would be a ~25c pricing error (see the `fairvalue` module note).
                let asset = crate::fastspot::asset_of_series(&mk.index_id)
                    .or_else(|| crate::fastspot::asset_of_series(ticker.split('-').next().unwrap_or("")));
                let Some(ret_bps) = asset.and_then(|a| spot.ret_bps(
                    a, now, (now - tick.source_us).max(1), p.spot_max_age_us, p.spot_min_venues,
                )) else { fair_no_spot += 1; return None };
                let level = crate::fairvalue::anchored_level(tick, ret_bps)?;
                let tau_s = (mk.close_unix_ms - now / 1_000) as f64 / 1_000.0;
                let fv = crate::fairvalue::digital_cents(level, mk.strike, tau_s, sigma)?;
                // ⚑ The derived entry gate, replacing the width gate. Both tolls carry phi(z),
                // so this admits a band in |z| — and at the measured ~1 bp of level precision
                // that band is the wing, not the mid band. A seat refused here is refused
                // because the model's own resolution is coarser than the income, which is a
                // statement about the instrument, not about our speed.
                let (ok, need) = crate::fairvalue::quotable(level, mk.strike, tau_s, sigma,
                    p.fair_reaction_s, p.fair_level_precision_bp, p.fair_gross_c)?;
                if !ok { fair_below_gross += 1; }
                // The margin is the derived toll plus the declared profit/fee. It is
                // phi(z)-shaped, so the model widens exactly where a width gate had nothing to
                // say, and collapses in the wing where the width gate would also have widened.
                let pair = if ok {
                    crate::fairvalue::pair(fv, mk.pos_fp, need + p.fair_margin_c,
                        p.fair_skew_c, bid, ask, &mk.ranges)
                } else {
                    crate::fairvalue::Pair::default()
                };
                if pair.bid.is_some() || pair.ask.is_some() { fair_quotes += 1; }
                if pair.bid.is_some() != pair.ask.is_some() { fair_one_sided += 1; }
                j.row("fair", json!({"ticker": ticker, "fv_c": fv, "level": level, "strike": mk.strike,
                    "quotable": ok, "need_c": need,
                    "tau_s": tau_s, "sigma_ann": sigma * (365.0 * 86_400.0f64).sqrt(),
                    "delta_c_per_bp": crate::fairvalue::delta_c_per_bp(level, mk.strike, tau_s, sigma),
                    "ret_bps": ret_bps, "index_age_ms": (now - tick.recv_us) / 1_000,
                    "bid": pair.bid, "ask": pair.ask, "their_bid": bid, "their_ask": ask, "mid": mid}));
                Some(pair)
            }).flatten();
            // Diagnostic the shadow could not see: another maker improving past our live quote.
            for (i, own) in [(BID, own_bid), (ASK, own_ask)] {
                let Some(px) = own else { continue };
                let passed = if i == BID { bid > px } else { ask < px };
                if passed {
                    let coid = mk.slots[i].clone().unwrap_or_default();
                    if undercut_seen.insert(coid.clone()) {
                        undercuts += 1;
                        j.row("undercut", json!({"coid": coid, "our_px": px, "their_px": if i == BID { bid } else { ask }, "vt": vt}));
                    }
                }
            }
            for i in [BID, ASK] {
                let side_px = if i == BID { bid } else { ask };
                // Improving a bid steps UP from its own level; improving an ask steps DOWN, so its
                // step is the grid's just below it. At ask 0.9000 that is 1c, not the 0.1c above it:
                // 0.8990 is not a price (1,649 invalid_price rejects on run 9, 2026-09-26).
                let tick = if i == BID { tick_at(&mk.ranges, side_px) } else { tick_at(&mk.ranges, side_px - 1) };
                // Sports: the flattening side only needs the game to be quiet; opening also needs
                // width and the band.
                let exiting = (i == ASK && mk.pos_fp > 0) || (i == BID && mk.pos_fp < 0);
                let fair_side = fair.and_then(|f| if i == BID { f.bid } else { f.ask });
                let mut want = if p.fair {
                    // Continuous: no width term, no `penny_room`, no residual. The only entry
                    // condition is that the model produced a price for THIS side — which is the
                    // refusal rule, and is how the seat goes one-sided on a disagreement. The
                    // close buffers still apply: they are risk controls, not entry gates, and
                    // `tau <= 60 s` is also where the pricer itself stops having an opinion.
                    let close_ok = if exiting {
                        mk.close_unix_ms - now / 1_000 > p.exit_stop_before_close_s * 1_000
                    } else {
                        open_ok
                    };
                    fair_side.is_some() && close_ok
                } else if p.sports.is_some() {
                    if exiting { sports_ok } else { sports_open }
                } else if p.penny_room > 0 {
                    let in_band = mid > 1.0 && mid < 99.0;
                    if exiting {
                        // See `exit_ignore_room`: the reducing leg keeps quoting into a tight book
                        // and through the second half of the market, because that is where the
                        // counterparty actually was.
                        let room_ok = p.exit_ignore_room || ask - bid >= p.penny_room * tick;
                        let close_ok = mk.close_unix_ms - now / 1_000
                            > p.exit_stop_before_close_s * 1_000;
                        room_ok && close_ok && in_band
                    } else {
                        open_ok && ask - bid >= p.penny_room * tick && in_band
                    }
                } else {
                    postable
                };
                // Count the clip we are about to post: a fractional position (−0.98 after a
                // partial fill) must not admit a clip that ends at −1.98 (happened 2026-09-23).
                if i == BID && mk.pos_fp + p.clip_fp > p.max_pos_fp { want = false; }
                if i == ASK && mk.pos_fp - p.clip_fp < -p.max_pos_fp { want = false; }
                // A bid opens/adds unless we are short; an ask opens/adds unless we are long.
                let opens = if i == BID { mk.pos_fp >= 0 } else { mk.pos_fp <= 0 };
                if opens && mk.close_unix_ms - now / 1_000 <= p.open_cutoff_s * 1_000 { want = false; }
                if round_other.is_some_and(|o| round_cap_blocks(o + mk.pos_fp, i == BID, p.clip_fp, p.max_round_net_fp)) {
                    want = false;
                }
                if opens && !opening_deadline_ok(deadline.saturating_duration_since(Instant::now()), p.open_cutoff_s) { want = false; }
                if let (Some(cfg), true) = (&p.sports, want && opens) {
                    // Worst case with THIS side resting: every other market of the game at its worst
                    // one-sided outcome, this one with side i at a full clip.
                    let rest = |k: usize| mk.slots[k].as_ref().and_then(|c| orders.get(c)).map_or(0, |o| o.remaining_fp);
                    let (b, a) = if i == BID { (p.clip_fp, rest(ASK)) } else { (rest(BID), p.clip_fp) };
                    let this = (mk.pos_fp + b).abs().max((mk.pos_fp - a).abs());
                    let (g_other, t_other) = exposure_elsewhere.unwrap_or((0, 0));
                    if g_other + this > cfg.max_game_fp || t_other + this > cfg.max_total_fp { want = false; }
                }
                // Under `--fair` the momentum half of this gate is dropped and the thin-side half
                // is kept, because they are not the same kind of signal. `mom` is the Kalshi
                // mid's own 1 s change — a lagging restatement of the spot move the model has
                // already priced from upstream of Kalshi's publisher — and gating a model price
                // on a cruder copy of its own input is `gate-on-signal-over-cost-is-an-anti-gate`.
                // Book imbalance is genuinely orthogonal: `fairvalue` has no book term at all.
                let toxic = if p.fair {
                    if i == ASK { bsz as f64 / tot > p.thin_pull } else { asz as f64 / tot > p.thin_pull }
                } else if i == ASK {
                    mom > p.mom_pull_c || bsz as f64 / tot > p.thin_pull
                } else {
                    -mom > p.mom_pull_c || asz as f64 / tot > p.thin_pull
                };
                if toxic { want = false; }
                // Likewise the spot pull: it is a threshold on exactly the quantity the model
                // integrates continuously, and the refusal rule already acts on it with a price
                // instead of a binary. Running both gates the seat twice on one signal.
                if want && p.spot_bps > 0.0 && !p.fair {
                    let series = ticker.split('-').next().unwrap_or("");
                    if let Some(r) = crate::fastspot::asset_of_series(series).and_then(|a| {
                        spot.ret_bps(a, now, p.spot_window_us, p.spot_max_age_us, p.spot_min_venues)
                    }) {
                        if (i == ASK && r > p.spot_bps) || (i == BID && r < -p.spot_bps) { want = false; }
                    }
                }
                let mut target = match (p.penny_room > 0, i == BID) {
                    (true, true) => bid + tick,
                    (true, false) => ask - tick,
                    (false, true) => bid,
                    (false, false) => ask,
                };
                // The model's price, already on the grid and already clamped post-only inside
                // the others' touch by `fairvalue::pair`.
                if let Some(px) = fair_side { target = px; }
                if let Some(cfg) = &p.sports {
                    // Inside only when there is room for both of our quotes plus a tick; else join.
                    if ask - bid < cfg.penny_min_c * 100 { target = if i == BID { bid } else { ask }; }
                    if exiting && mk.entry_px > 0 {
                        let held = now - mk.entry_us;
                        let edge = if held < cfg.scratch_us { cfg.exit_edge_c * 100 } else if held < cfg.bail_us { 0 } else { i64::MIN / 4 };
                        if edge > i64::MIN / 4 {
                            // Long YES: sell no lower than entry + edge. Short YES: buy no higher than entry - edge.
                            target = if i == ASK { target.max(mk.entry_px + edge) } else { target.min(mk.entry_px - edge) };
                        }
                        // Post-only must not cross the others' opposite touch.
                        target = if i == ASK { target.max(bid + tick) } else { target.min(ask - tick) };
                    }
                }
                if p.penny_room > 0 && exiting {
                    target = exit_target(i, target, mk.entry_px, p.exit_min_edge_c, bid, ask, tick);
                }
                if p.book_residual {
                    want &= anchor.is_some_and(|value| book_residual_allows(i, target, value));
                    if exiting && mk.entry_px > 0 {
                        want &= nonnegative_pair_price(mk.pos_fp, i, target, mk.entry_px);
                    }
                }
                // Sports: our OWN price must sit in the band too (a 6c bid under a 30c ask has
                // an in-band mid; the ledger's band was on the quote price).
                if p.sports.is_some() && !exiting && ((target as f64) < p.mid_lo_c * 100.0 || (target as f64) > p.mid_hi_c * 100.0) { want = false; }
                if opens && !open_band_ok(target, p.open_lo_c, p.open_hi_c) { want = false; }
                if target <= 0 || target >= PRICE_SCALE { want = false; }
                let cur = mk.slots[i].as_ref().and_then(|c| orders.get(c));
                match cur {
                    Some(o) if matches!(o.st, St::PendingCancel | St::PendingNew | St::PendingAmend) => continue,
                    Some(o) if want && o.price == target => continue,
                    // A model price moves continuously, so exact equality would chase every index
                    // tick at 10 tokens an amend: 300 tokens/s is 30 amends/s for the whole book.
                    // Hold the quote until the model has moved `fair_requote_c`. At the measured
                    // 2.83 c/bp this threshold is also the quote's resolution in spot terms.
                    Some(o) if want && p.fair && p.fair_requote_c > 0.0
                        && ((o.price - target).abs() as f64) < p.fair_requote_c * 100.0 => continue,
                    // Untouched order, new price: one amend (keeps order_id) instead of two legs.
                    // A partly filled order is cancelled instead: amend's `count` semantics on a
                    // partial fill are unmeasured.
                    Some(o) if want && (p.amend || p.amend_only) && o.st == St::Resting
                        && o.remaining_fp == p.clip_fp && o.order_id.is_some() => {
                        if tokens < 10.0 { continue; }
                        let c = o.coid.clone();
                        j.row("amend", json!({"coid": c, "price": target, "mid": mid}));
                        if request_amend(&http, &auth, &done_tx, &mut orders, &c, target, p.clip_fp, &mut amends) {
                            tokens -= 10.0;
                        }
                    }
                    // `want == false` is a gate refusing to quote here at all, so the order has to
                    // GO. Amend-only does not apply: parking it a few ticks away would leave a
                    // live quote that a cap, the close cutoff or the band just rejected. Amend
                    // expresses "move this quote"; only cancel expresses "there is no quote".
                    Some(o) => {
                        if tokens < 2.0 { continue; }
                        tokens -= 2.0;
                        let c = o.coid.clone();
                        request_cancel(&http, &auth, &done_tx, &mut orders, &c, p.exchange_index, &mut cancels);
                    }
                    None if want => {
                        if tokens < 10.0 { continue; }
                        tokens -= 10.0;
                        posts += 1;
                        let coid = crate::latency::random_id();
                        let body = json!({
                            "ticker": ticker, "client_order_id": coid, "side": if i == BID { "bid" } else { "ask" },
                            "count": format!("{:.2}", p.clip_fp as f64 / SIZE_SCALE as f64), "price": format!("{:.4}", target as f64 / PRICE_SCALE as f64),
                            "time_in_force": "good_till_canceled", "self_trade_prevention_type": "maker",
                            "post_only": true, "order_group_id": group_id, "exchange_index": p.exchange_index,
                        });
                        j.row("new", json!({"coid": coid, "body": body, "mid": mid, "mom": mom,
                            "anchor_c": anchor, "bsz": bsz, "asz": asz}));
                        orders.insert(coid.clone(), LiveOrder { coid: coid.clone(), order_id: None,
                            ticker: ticker.to_owned(), side: i, price: target, st: St::PendingNew, remaining_fp: p.clip_fp });
                        mk.slots[i] = Some(coid.clone());
                        let (http2, auth2, tx) = (http.clone(), auth.clone(), done_tx.clone());
                        timing.to_spawn_us.push(t_recv.elapsed().as_micros() as i64, 1.0);
                        tokio::spawn(async move {
                            let res = if dry() { Ok(json!({"order_id": body["client_order_id"].clone()})) } else { signed(&http2, &auth2, "POST", "/portfolio/events/orders", Some(&body)).await };
                            let _ = tx.send(Done::Created { coid, res });
                        });
                    }
                    None => {}
                }
            }
            if let (Some(cfg), Some(game)) = (&p.sports, jump_game) {
                jump_pauses += 1;
                j.row("jump_pause", json!({"game": game, "ticker": ticker, "vt": vt}));
                pause_game(&game, now + cfg.pause_us, &mut game_pause, &markets, &mut orders, &http, &auth, &done_tx, p.exchange_index, &mut cancels);
            }
        }
        // Feed broke: we are blind. Cancel everything before reconnecting.
        eprintln!("feed break: cancelling all resting orders");
        tokio::time::sleep(Duration::from_secs(2)).await; // in-flight creates land first
        cancel_everything(&http, &auth, &mut j, p.exchange_index).await;
        orders.clear();
        for m in markets.values_mut() { m.slots = [None, None]; }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }

    eprintln!("STOP: {stop_reason}. Cancelling everything.");
    // Let creates already in flight land first, or they rest AFTER the sweep (an XRP bid did,
    // 2026-09-25).
    tokio::time::sleep(Duration::from_secs(2)).await;
    j.row("stop", json!({"reason": stop_reason, "game_pauses": game_pauses, "jump_pauses": jump_pauses, "posts": posts, "cancels": cancels, "rejects": rejects, "fills": fills, "group_trips": group_trips, "undercuts": undercuts, "local_cash_c": cash_c}));
    cancel_everything(&http, &auth, &mut j, p.exchange_index).await;
    if !dry() { let _ = signed(&http, &auth, "DELETE", &format!("/portfolio/order_groups/{group_id}"), None).await; }
    for t in &spot_tasks { t.abort(); }
    if let Some(t) = auto_task { t.abort(); }
    j.row("round_trips", json!({"n": round_trips, "pnl_c": rt_pnl_c}));
    let eq = equity_c(&http, &auth, p.exchange_index).await.unwrap_or(f64::NAN);
    let session = realized_c + markets.values().map(Mkt::mtm_c).sum::<f64>();
    j.row("end", json!({"venue_equity_c": eq, "session_mtm_c": session, "cumulative_c": equity0 - baseline + session}));
    eprintln!("session (own ledger, marked) {session:+.2}c, cumulative {:+.2}c", equity0 - baseline + session);
    j.finish();
    eprintln!("end equity {eq:.2}c, session {:+.2}c, cumulative {:+.2}c (open positions ride to settlement)", eq - equity0, eq - baseline);
    Ok(stop_reason)
}

/// Cancel every order of ours in one game's markets and block posting there until `until`.
#[allow(clippy::too_many_arguments)]
fn pause_game(
    game: &str, until: i64, game_pause: &mut HashMap<String, i64>, markets: &HashMap<String, Mkt>,
    orders: &mut HashMap<String, LiveOrder>, http: &reqwest::Client, auth: &Arc<Auth>,
    tx: &mpsc::UnboundedSender<Done>, exchange_index: i64, cancels: &mut u64,
) {
    game_pause.insert(game.to_owned(), until);
    let victims: Vec<String> = orders.values()
        .filter(|o| markets.get(&o.ticker).is_some_and(|m| m.game == game))
        .map(|o| o.coid.clone()).collect();
    for c in victims { request_cancel(http, auth, tx, orders, &c, exchange_index, cancels); }
}

/// Sports discovery: every open market of the quoted series (tag- and shard-filtered, capped),
/// plus the parent books of the games admitted. Markets no longer open are dropped (their
/// ledger value locked in, their order slots released — the venue cancels resting orders on
/// close). Returns tickers that were newly added. A failed page keeps the current set.
#[allow(clippy::too_many_arguments)]
async fn refresh_sports(
    http: &reqwest::Client, series: &[String], cfg: &SportsLive, exchange_index: i64,
    markets: &mut HashMap<String, Mkt>, orders: &mut HashMap<String, LiveOrder>,
    realized_c: &mut f64, j: &mut Journal,
) -> Vec<String> {
    let tagged = |t: &str| cfg.tags.iter().any(|g| t.contains(&format!("-{g}")));
    let mut open: Vec<(Value, bool)> = Vec::new();
    for (list, parent) in [(series, false), (cfg.parents.as_slice(), true)] {
        for s in list {
            let mut cursor = String::new();
            loop {
                let url = format!("{}/markets?series_ticker={s}&status=open&limit=1000&cursor={cursor}", rest_base());
                let body: Value = match http.get(url).send().await {
                    Ok(r) if r.status().is_success() => match r.json().await { Ok(b) => b, Err(_) => return Vec::new() },
                    _ => { eprintln!("sports refresh {s}: failed, keeping the current set"); return Vec::new(); }
                };
                for m in body["markets"].as_array().into_iter().flatten() {
                    let t = m["ticker"].as_str().unwrap_or("");
                    if tagged(t) && m["exchange_index"].as_i64() == Some(exchange_index) { open.push((m.clone(), parent)); }
                }
                cursor = body["cursor"].as_str().unwrap_or("").to_owned();
                if cursor.is_empty() { break; }
            }
            tokio::time::sleep(Duration::from_millis(120)).await;
        }
    }
    apply_sports(open, cfg, markets, orders, realized_c, j, false)
}

/// Reconcile the tracked set with a fresh discovery (already filtered; auto lists come ranked).
/// In `evict` mode a tracked market that fell out of the list is dropped when it holds no
/// position and no order of ours, so a continuous run follows where the volume is.
fn apply_sports(
    open: Vec<(Value, bool)>, cfg: &SportsLive, markets: &mut HashMap<String, Mkt>,
    orders: &mut HashMap<String, LiveOrder>, realized_c: &mut f64, j: &mut Journal, evict: bool,
) -> Vec<String> {
    let open_set: std::collections::HashSet<String> = open.iter().filter_map(|(m, _)| m["ticker"].as_str().map(str::to_owned)).collect();
    let gone: Vec<String> = markets.iter()
        .filter(|(t, m)| !open_set.contains(*t) && (!evict || (m.pos_fp == 0 && m.slots.iter().all(Option::is_none))))
        .map(|(t, _)| t.clone()).collect();
    for t in &gone {
        if let Some(m) = markets.remove(t) {
            *realized_c += m.mtm_c();
            for c in m.slots.iter().flatten() { orders.remove(c); }
        }
    }
    let mut new = Vec::new();
    let mut quotable = markets.values().filter(|m| !m.parent).count();
    for (m, parent) in open.iter().filter(|(_, p)| !*p) .chain(open.iter().filter(|(_, p)| *p)) {
        let t = m["ticker"].as_str().unwrap_or("").to_owned();
        if markets.contains_key(&t) { continue; }
        let game = t.split('-').nth(1).unwrap_or("").to_owned();
        if *parent {
            if !markets.values().any(|x| !x.parent && x.game == game) { continue; }
        } else {
            if quotable >= cfg.max_markets { continue; }
            quotable += 1;
        }
        let close = m["close_time"].as_str().and_then(rfc3339_us).map_or(i64::MAX / 4, |c| c / 1_000);
        markets.insert(t.clone(), Mkt {
            close_unix_ms: close, ranges: price_ranges(m), game, parent: *parent, conservative: true,
            strike: m["floor_strike"].as_f64().unwrap_or(0.0),
            index_id: crate::fairvalue::index_id_of_series(
                t.split('-').next().unwrap_or("")).unwrap_or_default(),
            ..Default::default()
        });
        new.push(t);
    }
    j.row("sports_markets", json!({"added": new.len(), "dropped": gone.len(), "total": markets.len(), "quotable": quotable}));
    eprintln!("sports refresh: +{} -{} markets, {} tracked ({} quotable)", new.len(), gone.len(), markets.len(), quotable);
    new
}

/// Rolling date tags as they appear in tickers (`26SEP27` = 2026-09-27): yesterday..tomorrow UTC.
fn utc_tags() -> Vec<String> {
    const MON: [&str; 12] = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
    let today = unix_us() / 86_400_000_000;
    (-1..=1).map(|d| {
        // Civil-from-days (H. Hinnant), days since 1970-01-01.
        let z = today + d + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z - era * 146_097;
        let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let day = doy - (153 * mp + 2) / 5 + 1;
        let month = if mp < 10 { mp + 3 } else { mp - 9 };
        let year = yoe + era * 400 + i64::from(month <= 2);
        format!("{:02}{}{:02}", year % 100, MON[(month - 1) as usize], day)
    }).collect()
}

/// Background sweep of every open sports event: fee-free series, our shard, a date tag in
/// the ticker, a tight enough in-band book, enough 24h volume. Ranked by 24h volume.
async fn auto_discover(
    http: reqwest::Client, free: std::collections::HashSet<String>, exchange_index: i64,
    refresh_s: u64, max_spread_c: i64, min_v24: f64, tx: mpsc::Sender<Vec<Value>>,
) {
    let f = |m: &Value, k: &str| m[k].as_str().and_then(|x| x.parse::<f64>().ok());
    loop {
        let tags: Vec<String> = utc_tags().into_iter().map(|t| format!("-{t}")).collect();
        let mut picked: Vec<(f64, Value)> = Vec::new();
        let mut cursor = String::new();
        let mut ok = true;
        for _ in 0..400 {
            let url = format!("{}/events?status=open&with_nested_markets=true&limit=200&cursor={cursor}", rest_base());
            let body: Value = match http.get(&url).send().await {
                Ok(r) if r.status().is_success() => match r.json().await { Ok(b) => b, Err(_) => { ok = false; break; } },
                Ok(r) if r.status().as_u16() == 429 => { tokio::time::sleep(Duration::from_secs(2)).await; continue; }
                _ => { ok = false; break; }
            };
            for ev in body["events"].as_array().into_iter().flatten() {
                if !ev["series_ticker"].as_str().is_some_and(|s| free.contains(s)) { continue; }
                for m in ev["markets"].as_array().into_iter().flatten() {
                    let t = m["ticker"].as_str().unwrap_or("");
                    if m["exchange_index"].as_i64() != Some(exchange_index) || !tags.iter().any(|g| t.contains(g.as_str())) { continue; }
                    if m["status"].as_str().is_some_and(|s| s != "active") { continue; }
                    let (Some(b), Some(a)) = (f(m, "yes_bid_dollars"), f(m, "yes_ask_dollars")) else { continue };
                    let v = f(m, "volume_24h_fp").unwrap_or(0.0);
                    let mid = (a + b) * 50.0;
                    if b <= 0.0 || a >= 1.0 || (a - b) * 100.0 > max_spread_c as f64 + 0.01 || !(5.0..=95.0).contains(&mid) || v < min_v24 { continue; }
                    picked.push((v, m.clone()));
                }
            }
            cursor = body["cursor"].as_str().unwrap_or("").to_owned();
            if cursor.is_empty() { break; }
            tokio::time::sleep(Duration::from_millis(150)).await;
        }
        if ok {
            picked.sort_by(|a, b| b.0.total_cmp(&a.0));
            if tx.send(picked.into_iter().map(|(_, m)| m).collect()).await.is_err() { return; }
        } else {
            eprintln!("auto discovery: sweep failed, keeping the current set");
        }
        tokio::time::sleep(Duration::from_secs(refresh_s)).await;
    }
}

fn release(orders: &mut HashMap<String, LiveOrder>, markets: &mut HashMap<String, Mkt>, coid: &str) {
    if let Some(o) = orders.remove(coid) {
        if let Some(m) = markets.get_mut(&o.ticker) {
            if m.slots[o.side].as_deref() == Some(coid) { m.slots[o.side] = None; }
        }
    }
}

fn request_cancel(
    http: &reqwest::Client, auth: &Arc<Auth>, tx: &mpsc::UnboundedSender<Done>,
    orders: &mut HashMap<String, LiveOrder>, coid: &str, exchange_index: i64, cancels: &mut u64,
) {
    let Some(o) = orders.get_mut(coid) else { return };
    if o.st == St::PendingCancel { return; }
    o.st = St::PendingCancel;
    *cancels += 1;
    // If the create has not been acked yet we cannot name the order; the ack handler cancels it.
    if let Some(id) = o.order_id.clone() {
        spawn_cancel(http, auth, tx, coid.to_owned(), id, o.ticker.clone(), exchange_index);
    }
}

/// Move a resting order to `target` with one amend, keeping its `order_id`.
///
/// Returns `false` when an amend cannot express the move, and the caller MUST then fall back to a
/// cancel: a partially filled order (amend's `count` semantics on a partial are unmeasured), an
/// order with no id yet, or a price off the 1..PRICE_SCALE grid. The distinction matters because
/// this is also the pull path, and a risk control that silently does nothing is the failure mode
/// this repository has hit most often.
/// Can one amend say "this order now rests at `target`"? Factored out so the refusal cases are
/// testable without a credential or a socket, because they are the cases where a pull would
/// silently not happen.
fn amendable(o: &LiveOrder, target: i64, clip_fp: i64) -> bool {
    o.st == St::Resting
        && o.remaining_fp == clip_fp
        && o.order_id.is_some()
        && target > 0
        && target < PRICE_SCALE
}

#[allow(clippy::too_many_arguments)]
fn request_amend(
    http: &reqwest::Client, auth: &Arc<Auth>, tx: &mpsc::UnboundedSender<Done>,
    orders: &mut HashMap<String, LiveOrder>, coid: &str, target: i64, clip_fp: i64,
    amends: &mut u64,
) -> bool {
    let Some(o) = orders.get_mut(coid) else { return false };
    if !amendable(o, target, clip_fp) { return false }
    let Some(id) = o.order_id.clone() else { return false };
    if o.price == target { return true; }
    let body = json!({"ticker": o.ticker, "side": if o.side == BID { "bid" } else { "ask" },
        "price": format!("{:.4}", target as f64 / PRICE_SCALE as f64),
        "count": format!("{:.2}", clip_fp as f64 / SIZE_SCALE as f64)});
    o.st = St::PendingAmend;
    *amends += 1;
    let (http, auth, tx, coid) = (http.clone(), auth.clone(), tx.clone(), coid.to_owned());
    tokio::spawn(async move {
        let res = if dry() { Ok(json!({})) } else {
            signed(&http, &auth, "POST", &format!("/portfolio/events/orders/{id}/amend"), Some(&body)).await
        };
        let _ = tx.send(Done::Amended { coid, price: target, res });
    });
    true
}

fn spawn_cancel(http: &reqwest::Client, auth: &Arc<Auth>, tx: &mpsc::UnboundedSender<Done>,
    coid: String, id: String, ticker: String, exchange_index: i64) {
    let (http, auth, tx) = (http.clone(), auth.clone(), tx.clone());
    tokio::spawn(async move {
        let path = format!("/portfolio/events/orders/{id}?market_ticker={ticker}&exchange_index={exchange_index}");
        let res = if dry() { Ok(json!({})) } else { signed(&http, &auth, "DELETE", &path, None).await };
        let _ = tx.send(Done::Cancelled { coid, res });
    });
}

/// Standalone kill: cancel every resting order on the account and verify the book is empty.
/// Places nothing. For cleanup when an engine exited with an order still in flight.
pub async fn cancel_all(auth: Auth, exchange_index: i64) -> Result<()> {
    let http = client()?;
    let tmp = std::env::temp_dir().join(format!("cancel_all_{}.jsonl.gz", unix_us()));
    let mut j = Journal(BufWriter::new(GzEncoder::new(File::create(&tmp)?, Compression::fast())), Samples::new(TIMING_CAP));
    cancel_everything(&http, &auth, &mut j, exchange_index).await;
    j.finish();
    Ok(())
}

async fn cancel_everything(http: &reqwest::Client, auth: &Auth, j: &mut Journal, exchange_index: i64) {
    if dry() { eprintln!("dry run: nothing to cancel"); return; }
    // Five lists, four cancel rounds: the last list only verifies. Back off between attempts —
    // three sweeps 10 ms apart on 2026-09-25 retried inside the same failure and left 2 resting.
    for attempt in 0..5u64 {
        if attempt > 0 { tokio::time::sleep(Duration::from_millis(250 * attempt)).await; }
        let open = match signed(http, auth, "GET", "/portfolio/orders?status=resting", None).await {
            Ok(v) => v,
            Err(e) => { eprintln!("list resting failed: {e:#}"); continue; }
        };
        // Only this shard's orders: a second engine on another shard must survive our shutdown.
        let list: Vec<(String, String)> = open["orders"].as_array().into_iter().flatten()
            .filter(|o| o["exchange_index"].as_i64().is_none_or(|x| x == exchange_index))
            .filter_map(|o| Some((o["order_id"].as_str()?.to_owned(), o["ticker"].as_str()?.to_owned()))).collect();
        j.row("sweep", json!({"attempt": attempt, "resting": list.len()}));
        if list.is_empty() { eprintln!("verified: no resting orders"); return; }
        if attempt == 4 { break; }
        let body = json!({"orders": list.iter().map(|(id, t)| json!({"order_id": id, "market_ticker": t, "exchange_index": exchange_index})).collect::<Vec<_>>()});
        // The batch call can return 200 while cancelling nothing (per-order errors ride inside the
        // body — seen 2026-09-25), so log its body and ALWAYS follow with single cancels.
        match signed(http, auth, "DELETE", "/portfolio/events/orders/batched", Some(&body)).await {
            Ok(v) => j.row("batch_cancel", v),
            Err(e) => eprintln!("batch cancel failed: {e:#}"),
        }
        for (id, t) in &list {
            let path = format!("/portfolio/events/orders/{id}?market_ticker={t}&exchange_index={exchange_index}");
            if let Err(e) = signed(http, auth, "DELETE", &path, None).await {
                eprintln!("cancel {id} on {t} failed: {e:#}");
                j.row("cancel_failed", json!({"order_id": id, "ticker": t, "error": format!("{e:#}")}));
            }
        }
    }
    eprintln!("WARNING: could not verify an empty book after 4 cancel rounds — check the account");
}

/// Venue equity in cents: cash on the trading shard + NET exposure of open positions. It
/// UNDERSTATES while pairs are open (a YES+NO pair nets to exposure 0 until settlement), so it is
/// only a conservative startup / cumulative reference; the running caps use the engine's own
/// ledger. (Valuing holdings at `total_traded_dollars` instead OVERSTATED them 3x — it counts
/// turnover — and phantom-tripped the session cap on 2026-09-25.)
async fn equity_c(http: &reqwest::Client, auth: &Auth, exchange_index: i64) -> Result<f64> {
    let bal = signed(http, auth, "GET", "/portfolio/balance", None).await?;
    let shard = bal["balance_breakdown"].as_array().into_iter().flatten()
        .find(|b| b["exchange_index"].as_i64() == Some(exchange_index))
        .and_then(|b| b["balance"].as_str()).context("no balance on trading shard")?;
    let cash: f64 = shard.parse::<f64>()? * 100.0;
    let pos = signed(http, auth, "GET", "/portfolio/positions?count_filter=position&limit=200", None).await?;
    let exposure: f64 = pos["market_positions"].as_array().into_iter().flatten()
        .filter(|m| m["exchange_index"].as_i64() == Some(exchange_index))
        .filter_map(|m| m["market_exposure_dollars"].as_str()?.parse::<f64>().ok()).sum::<f64>() * 100.0;
    Ok(cash + exposure)
}

/// Seed positions from the venue, so a restart never forgets inventory it is still holding.
async fn sync_positions(http: &reqwest::Client, auth: &Auth, markets: &mut HashMap<String, Mkt>) -> Result<()> {
    let pos = signed(http, auth, "GET", "/portfolio/positions?count_filter=position&limit=200", None).await?;
    for m in pos["market_positions"].as_array().into_iter().flatten() {
        let (Some(t), Some(p)) = (m["ticker"].as_str(), m["position_fp"].as_str()) else { continue };
        if let (Some(mk), Ok(p)) = (markets.get_mut(t), parse_value(Some(&json!(p)), SIZE_SCALE)) {
            mk.pos_fp = p;
        }
    }
    Ok(())
}

fn price_ranges(m: &Value) -> Vec<(i64, i64, i64)> {
    m["price_ranges"].as_array().into_iter().flatten().filter_map(|r| {
        let f = |k: &str| parse_value(r.get(k), PRICE_SCALE).ok();
        Some((f("start")?, f("end")?, f("step")?))
    }).collect()
}

/// Price step at `px` on this grid; 1c if the grid is unknown (coarser never invents a price).
/// Where a pulled quote parks: `ticks` steps away from its own price on its own side — a bid
/// steps DOWN, an ask steps UP — walking the venue's grid, whose step is 0.1c in the wings and
/// 1c in the middle on crypto. A bid's step down is the grid's just below it, which is why the
/// two sides read `tick_at` at different prices (1,649 `invalid_price` rejects on 2026-09-26
/// came from getting exactly this backwards).
fn pull_price(ranges: &[(i64, i64, i64)], side: usize, price: i64, ticks: i64) -> i64 {
    let mut px = price;
    for _ in 0..ticks.max(0) {
        let next = if side == BID { px - tick_at(ranges, px - 1) } else { px + tick_at(ranges, px) };
        if next <= 0 || next >= PRICE_SCALE { break }
        px = next;
    }
    px
}

/// The settlement index id for a series ticker, for the spot-tick reprice path, which has an
/// asset in hand rather than a market. Empty for a non-crypto series, which never matches a
/// subscribed index and so prices nothing.
fn mk_index_id(series: &str) -> String {
    crate::fairvalue::index_id_of_series(series).unwrap_or_default()
}

fn tick_at(ranges: &[(i64, i64, i64)], px: i64) -> i64 {
    ranges.iter().find(|(a, b, _)| *a <= px && px < *b)
        .or_else(|| ranges.last().filter(|(_, b, _)| px == *b))
        .map_or(100, |(_, _, st)| *st)
}

async fn refresh_markets(http: &reqwest::Client, series: &[String], markets: &mut HashMap<String, Mkt>) {
    let now_ms = unix_us() / 1_000;
    for s in series {
        let url = format!("{}/markets?series_ticker={s}&status=open&limit=5", rest_base());
        let Ok(r) = http.get(url).send().await else { continue };
        let Ok(body) = r.json::<Value>().await else { continue };
        for m in body["markets"].as_array().into_iter().flatten() {
            let (Some(t), Some(c)) = (m["ticker"].as_str(), m["close_time"].as_str().and_then(rfc3339_us)) else { continue };
            if c / 1_000 > now_ms && !markets.contains_key(t) {
                markets.insert(t.to_owned(), Mkt {
                    close_unix_ms: c / 1_000, ranges: price_ranges(m),
                    strike: m["floor_strike"].as_f64().unwrap_or(0.0),
                    index_id: crate::fairvalue::index_id_of_series(
                        t.split('-').next().unwrap_or("")).unwrap_or_default(),
                    ..Default::default()
                });
            }
        }
    }
}

pub async fn signed(http: &reqwest::Client, auth: &Auth, method: &str, path: &str, body: Option<&Value>) -> Result<Value> {
    let h = auth.headers(method, &format!("/trade-api/v2{path}"));
    let url = format!("{}{path}", rest_base());
    let mut req = match method {
        "POST" => http.post(url),
        "PUT" => http.put(url),
        "DELETE" => http.delete(url),
        _ => http.get(url),
    }
    .header("KALSHI-ACCESS-KEY", h.key_id)
    .header("KALSHI-ACCESS-TIMESTAMP", h.timestamp)
    .header("KALSHI-ACCESS-SIGNATURE", h.signature);
    if let Some(b) = body { req = req.json(b); }
    let resp = req.send().await?;
    let status = resp.status();
    let text = resp.text().await?;
    if !status.is_success() { bail!("{method} {path} → {status}: {text}"); }
    Ok(if text.is_empty() { Value::Null } else { serde_json::from_str(&text)? })
}

#[cfg(test)]
mod tests {
    use super::{ASK, BID, Mkt};
    use serde_json::Value;

    /// The amend-only park price. Crypto steps 0.1c below 10c and above 90c and 1c between, so a
    /// fixed "3 ticks" is three DIFFERENT distances depending on where the quote sits, and the
    /// two sides read the grid at different prices.
    #[test]
    fn a_pulled_quote_parks_ticks_away_on_its_own_side() {
        // The crypto grid in PRICE_SCALE (1e4) units: 0.1c wings, 1c middle.
        let g = vec![(0, 1_000, 10), (1_000, 9_000, 100), (9_000, 10_000, 10)];
        // Mid-band: a bid steps down 1c a tick, an ask steps up 1c a tick.
        assert_eq!(super::pull_price(&g, BID, 5_000, 3), 4_700);
        assert_eq!(super::pull_price(&g, ASK, 5_000, 3), 5_300);
        // Wing: the same three ticks are 0.3c, not 3c.
        assert_eq!(super::pull_price(&g, BID, 500, 3), 470);
        assert_eq!(super::pull_price(&g, ASK, 9_500, 3), 9_530);
        // A bid's step DOWN is the grid just below it: at exactly 10c the step down is the 0.1c
        // wing, not the 1c band the price itself sits in.
        assert_eq!(super::pull_price(&g, BID, 1_000, 1), 990);
        // An ask's step UP at 90c is the wing above it.
        assert_eq!(super::pull_price(&g, ASK, 9_000, 1), 9_010);
        // Never off the grid: a 1c bid cannot park below zero, and it stops rather than wrapping.
        assert_eq!(super::pull_price(&g, BID, 20, 5), 10);
        assert_eq!(super::pull_price(&g, ASK, 9_990, 5), 9_990);
        // Zero ticks is a no-op, and a negative count cannot move the quote the wrong way.
        assert_eq!(super::pull_price(&g, BID, 5_000, 0), 5_000);
        assert_eq!(super::pull_price(&g, ASK, 5_000, -3), 5_000);
    }

    /// The park price is measured from the OTHERS' touch so that a sustained move cannot walk our
    /// quote away a few ticks at a time, and so that a quote already clear of the touch is left
    /// alone instead of amended on every spot tick.
    #[test]
    fn a_pull_is_idempotent_against_the_touch() {
        let g = vec![(0, 1_000, 10), (1_000, 9_000, 100), (9_000, 10_000, 10)];
        let (touch_bid, touch_ask) = (4_000, 4_200);
        let park_bid = super::pull_price(&g, BID, touch_bid, 3);
        let park_ask = super::pull_price(&g, ASK, touch_ask, 3);
        assert_eq!((park_bid, park_ask), (3_700, 4_500));
        // A bid resting AT the touch is not clear and must move.
        assert!(!(touch_bid <= park_bid));
        // Once parked it IS clear, so the next spot tick leaves it alone: no walk, no tokens.
        assert!(park_bid <= park_bid);
        assert!(super::pull_price(&g, BID, park_bid, 3) < park_bid, "re-parking from our own \
            price would keep walking, which is why the touch is the reference");
        // Mirror image on the ask.
        assert!(!(touch_ask >= park_ask));
        assert!(park_ask >= park_ask);
    }

    /// Amend-only must never become a pull that does not happen: every case `amendable` refuses
    /// is a case the caller has to fall back to a cancel for.
    #[test]
    fn amend_refuses_what_it_cannot_express_so_the_pull_falls_back() {
        use super::{LiveOrder, PRICE_SCALE, St, amendable};
        let clip = 100;
        let o = |st, remaining_fp, order_id| LiveOrder { coid: "c".into(), order_id,
            ticker: "KXETH15M-x".into(), side: BID, price: 5_000, st, remaining_fp };
        let resting = || o(St::Resting, clip, Some("oid".into()));
        assert!(amendable(&resting(), 4_700, clip));
        // A partly filled order: amend's `count` semantics on a partial are unmeasured.
        assert!(!amendable(&o(St::Resting, clip / 2, Some("oid".into())), 4_700, clip));
        // Not resting yet, so there is nothing at the venue to amend.
        for st in [St::PendingNew, St::PendingCancel, St::PendingAmend] {
            assert!(!amendable(&o(st, clip, Some("oid".into())), 4_700, clip), "{st:?}");
        }
        // Acked but unnamed: the order id IS the amend path.
        assert!(!amendable(&o(St::Resting, clip, None), 4_700, clip));
        // Off the grid at either end.
        assert!(!amendable(&resting(), 0, clip));
        assert!(!amendable(&resting(), PRICE_SCALE, clip));
    }

    /// An exit must never rest through break-even, and must never cross the others' touch while
    /// doing so. Both halves have cost money: chasing the touch booked a certain loss on a seat
    /// built to collect a spread, and a clamp that ignores the touch is a post-only rejection.
    #[test]
    fn an_exit_rests_at_a_profit_without_crossing_the_touch() {
        let tick = 100;                         // 1c in PRICE_SCALE units
        let (bid, ask) = (4_200, 4_500);        // 42c / 45c, a 3c book
        let entry = 4_400;                      // we bought YES at 44c

        // Long YES: the penny ask would be 44c, which is break-even. With a 1c floor it moves to 45c.
        assert_eq!(super::exit_target(super::ASK, ask - tick, entry, 1.0, bid, ask, tick), 4_500);
        // 0c floor = never through break-even, but break-even itself is allowed.
        assert_eq!(super::exit_target(super::ASK, ask - tick, entry, 0.0, bid, ask, tick), 4_400);
        // Clamp off: it chases the touch, even to a locked loss. This is the old behaviour.
        let entry_high = 4_600;
        assert_eq!(super::exit_target(super::ASK, ask - tick, entry_high, -1.0, bid, ask, tick), 4_400);
        assert!(super::exit_target(super::ASK, ask - tick, entry_high, 0.0, bid, ask, tick) >= entry_high);

        // Never cross: in a one-tick book the clamped ask lands ON the ask, not inside the bid.
        let (tb, ta) = (4_200, 4_300);
        assert_eq!(super::exit_target(super::ASK, ta - tick, 4_000, 0.0, tb, ta, tick), 4_300);

        // Short YES mirrors: we sold at 44c, the bid exit must be at or below 43c and not cross the ask.
        assert_eq!(super::exit_target(super::BID, bid + tick, entry, 1.0, bid, ask, tick), 4_300);
        assert_eq!(super::exit_target(super::BID, bid + tick, 4_000, 0.0, bid, ask, tick), 4_000);
        assert_eq!(super::exit_target(super::BID, bid + tick, 9_000, 0.0, tb, ta, tick), 4_200);

        // No entry price recorded: nothing to clamp against, leave the target alone.
        assert_eq!(super::exit_target(super::ASK, ask - tick, 0, 0.0, bid, ask, tick), 4_400);
    }

    /// The post hold must end when the position authority lands, not on a timer — but it must
    /// still hold when the authority has NOT landed. These are the four orderings the two WS
    /// channels actually produce; the `fill`-first one is 14.9% of real fills.
    #[test]
    fn the_fill_message_releases_the_post_hold_in_either_arrival_order() {
        let backstop = 1_500_000;
        let held = |mk: &super::Mkt, now: i64| now < mk.hold_until_us;

        // user_order first: held until the fill lands, then free.
        let mut mk = super::Mkt::default();
        super::announce_fill(&mut mk, 0, backstop);
        assert!(held(&mk, 1_000), "must hold while the authority is outstanding");
        super::observe_fill(&mut mk);
        assert!(!held(&mk, 1_000), "the fill message must release the hold");

        // fill first (14.9% of real fills): the later user_order must not re-hold.
        let mut mk = super::Mkt::default();
        super::observe_fill(&mut mk);
        super::announce_fill(&mut mk, 0, backstop);
        assert!(!held(&mk, 1_000), "an already-seen fill must not start a hold");

        // Two fills announced, one message: still held. The second releases it.
        let mut mk = super::Mkt::default();
        super::announce_fill(&mut mk, 0, backstop);
        super::announce_fill(&mut mk, 0, backstop);
        super::observe_fill(&mut mk);
        assert!(held(&mk, 1_000), "one message cannot release two announced fills");
        super::observe_fill(&mut mk);
        assert!(!held(&mk, 1_000));

        // The backstop still bounds a fill message that never arrives.
        let mut mk = super::Mkt::default();
        super::announce_fill(&mut mk, 0, backstop);
        assert!(held(&mk, backstop - 1));
        assert!(!held(&mk, backstop));
    }

    #[test]
    fn round_cap_blocks_only_the_side_that_grows_the_bet() {
        let (ct, cap) = (100, 200); // 1 ct clips, cap 2 ct (SIZE_SCALE 100)
        // At +2 across the round: another YES would make +3, blocked; a sale shrinks it.
        assert!(super::round_cap_blocks(200, true, ct, cap));
        assert!(!super::round_cap_blocks(200, false, ct, cap));
        // Up to the cap is allowed; mirror image for a short round.
        assert!(!super::round_cap_blocks(100, true, ct, cap));
        assert!(super::round_cap_blocks(-200, false, ct, cap));
        assert!(!super::round_cap_blocks(-200, true, ct, cap));
        // Already past the cap (fills landed together): only the shrinking side quotes.
        assert!(super::round_cap_blocks(300, true, ct, cap));
        assert!(!super::round_cap_blocks(300, false, ct, cap));
    }

    #[test]
    fn tick_follows_the_venue_grid() {
        let v: Value = serde_json::from_str(r#"{"price_ranges":[{"end":"0.1000","start":"0.0000","step":"0.0010"},{"end":"0.9000","start":"0.1000","step":"0.0100"},{"end":"1.0000","start":"0.9000","step":"0.0010"}]}"#).unwrap();
        let r = super::price_ranges(&v);
        assert_eq!((super::tick_at(&r, 500), super::tick_at(&r, 5_000), super::tick_at(&r, 9_500)), (10, 100, 10));
        let flat: Value = serde_json::from_str(r#"{"price_ranges":[{"end":"1.0000","start":"0.0000","step":"0.0100"}]}"#).unwrap();
        assert_eq!(super::tick_at(&super::price_ranges(&flat), 500), 100); // COPPER wing: 1c
        assert_eq!(super::tick_at(&[], 500), 100); // unknown grid: never finer than 1c
        // Boundaries: an ask at 0.9000 improves by 1c (to 0.8900), a bid at 0.9000 by 0.1c.
        assert_eq!(super::tick_at(&r, 9_000 - 1), 100);
        assert_eq!(super::tick_at(&r, 9_000), 10);
        // An ask at 0.1000 improves by 0.1c (to 0.0990), a bid at 0.0990 by 0.1c (to 0.1000).
        assert_eq!(super::tick_at(&r, 1_000 - 1), 10);
        assert_eq!(super::tick_at(&r, 990), 10);
    }

    #[test]
    fn book_residual_rejects_a_touch_that_crossed_the_old_mid() {
        assert!(super::book_residual_allows(BID, 4_900, 50.0));
        assert!(!super::book_residual_allows(BID, 5_100, 50.0));
        assert!(super::book_residual_allows(ASK, 5_100, 50.0));
        assert!(!super::book_residual_allows(ASK, 4_900, 50.0));
    }

    #[test]
    fn open_band_keeps_the_wings_out() {
        assert!(super::open_band_ok(1_000, 10.0, 90.0)); // 10.00c is in
        assert!(!super::open_band_ok(990, 10.0, 90.0)); // 9.90c wing
        assert!(super::open_band_ok(8_900, 10.0, 90.0));
        assert!(!super::open_band_ok(9_000, 10.0, 90.0)); // 90.00c is out
        assert!(super::open_band_ok(9_950, 0.0, 100.0)); // default: off
        assert!(super::open_band_ok(10, 0.0, 100.0));
    }

    #[test]
    fn opening_stops_before_the_experiment_deadline() {
        assert!(super::opening_deadline_ok(std::time::Duration::from_secs(121), 120));
        assert!(!super::opening_deadline_ok(std::time::Duration::from_secs(120), 120));
        assert!(!super::opening_deadline_ok(std::time::Duration::from_secs(1), 120));
    }

    #[test]
    fn book_residual_never_locks_a_negative_pair() {
        assert!(super::nonnegative_pair_price(100, ASK, 5_100, 5_000));
        assert!(!super::nonnegative_pair_price(100, ASK, 4_900, 5_000));
        assert!(super::nonnegative_pair_price(-100, BID, 4_900, 5_000));
        assert!(!super::nonnegative_pair_price(-100, BID, 5_100, 5_000));
    }

    #[test]
    fn a_pair_is_worth_one_dollar_at_any_mid() {
        // Bought YES at 60 and NO at 45: a pair that cost 105 and pays 100 → −5 at every mid.
        let mut m = Mkt { yes_ct: 1.0, no_ct: 1.0, flow_c: -105.0, ..Default::default() };
        for mid in [1.0, 37.5, 50.0, 99.0] {
            m.last_mid = mid;
            assert!((m.mtm_c() + 5.0).abs() < 1e-9, "mid {mid}: {}", m.mtm_c());
        }
        // One unpaired YES bought at 20, marked at the mid.
        let mut n = Mkt { yes_ct: 1.0, flow_c: -20.0, last_mid: 35.0, ..Default::default() };
        assert!((n.mtm_c() - 15.0).abs() < 1e-9);
        n.last_mid = 0.0;
        assert!((n.mtm_c() + 20.0).abs() < 1e-9);
    }
}
