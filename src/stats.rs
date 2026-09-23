use serde_json::{Value, json};

/// Weighted samples of a duration in microseconds (or any i64), summarised on demand.
/// Capped so a multi-day run cannot grow without bound; the cap is reported.
pub struct Samples {
    values: Vec<(i64, f64)>,
    dropped: u64,
    cap: usize,
}

const QUANTILES: [f64; 7] = [0.01, 0.10, 0.25, 0.50, 0.75, 0.90, 0.99];

impl Samples {
    pub fn new(cap: usize) -> Self {
        Self { values: Vec::new(), dropped: 0, cap }
    }

    pub fn push(&mut self, value: i64, weight: f64) {
        if self.values.len() < self.cap {
            self.values.push((value, weight));
        } else {
            self.dropped += 1;
        }
    }

    /// Weighted quantiles plus the weighted share of samples strictly above each threshold.
    pub fn summary(&self, thresholds: &[i64]) -> Value {
        let mut sorted = self.values.clone();
        sorted.sort_unstable_by_key(|(v, _)| *v);
        let total: f64 = sorted.iter().map(|(_, w)| w).sum();
        if sorted.is_empty() || total <= 0.0 {
            return json!({"n": 0});
        }
        let mut q = serde_json::Map::new();
        let mut acc = 0.0;
        let mut qi = 0;
        for (v, w) in &sorted {
            acc += w;
            while qi < QUANTILES.len() && acc >= QUANTILES[qi] * total {
                q.insert(format!("p{}", (QUANTILES[qi] * 100.0).round()), json!(v));
                qi += 1;
            }
        }
        let above: serde_json::Map<String, Value> = thresholds
            .iter()
            .map(|t| {
                let w: f64 = sorted.iter().filter(|(v, _)| v > t).map(|(_, w)| w).sum();
                (format!(">{t}"), json!((w / total * 10_000.0).round() / 10_000.0))
            })
            .collect();
        json!({
            "n": sorted.len(),
            "weight": total,
            "dropped": self.dropped,
            "quantiles": q,
            "share_above": above,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weighted_quantiles_and_shares() {
        let mut s = Samples::new(100);
        s.push(1, 1.0);
        s.push(10, 3.0);
        let out = s.summary(&[5]);
        assert_eq!(out["quantiles"]["p10"], 1);
        assert_eq!(out["quantiles"]["p50"], 10);
        assert_eq!(out["share_above"][">5"], 0.75);
    }

    #[test]
    fn cap_counts_drops() {
        let mut s = Samples::new(1);
        s.push(1, 1.0);
        s.push(2, 1.0);
        assert_eq!(s.summary(&[])["dropped"], 1);
    }
}
