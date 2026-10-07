//! Research-only probability adjustment and bounded, lossless audit recorder.
use std::{collections::VecDeque, fs::{self, File}, io::{BufWriter, Write}, path::PathBuf,
    sync::mpsc::{self, SyncSender}, thread::{self, JoinHandle}};
use anyhow::{Result, bail};
use flate2::{Compression, write::GzEncoder};
use serde_json::Value;

const MAX_SPOT_CARRY_US: i64 = 20_000_000;

#[derive(Default)]
pub struct Spot {
    ticks: VecDeque<(i64, f64)>,
    origin: Option<i64>,
    cached: Option<(i64, Option<f64>)>,
}
impl Spot {
    pub fn push(&mut self, t: i64, px: f64) {
        if !px.is_finite() || px <= 0.0 { return; }
        self.origin.get_or_insert(t);
        self.ticks.push_back((t, px));
        while self.ticks.len() > 2 && self.ticks[1].0 < t - 70_000_000 { self.ticks.pop_front(); }
    }
    fn at(&self, t: i64) -> Option<f64> {
        let &(seen, px) = self.ticks.iter().rev().find(|(seen, _)| *seen <= t)?;
        (t-seen <= MAX_SPOT_CARRY_US).then_some(px)
    }
    fn variance(&mut self, now: i64) -> Option<f64> {
        let origin = self.origin?;
        let grid = origin + (now-origin).div_euclid(1_000_000)*1_000_000;
        if let Some((g, v)) = self.cached { if grid == g { return v; } }
        let value = (|| {
            if grid < origin + 61_000_000 { return None; }
            let mut sum = 0.0;
            let mut prev = self.at(grid-60_000_000)?;
            for j in 1..=60 {
                let px = self.at(grid-(60-j)*1_000_000)?;
                sum += (px/prev).ln().powi(2);
                prev=px;
            }
            (sum > 0.0).then_some(sum/60.0)
        })();
        self.cached=Some((grid,value));
        value
    }
    pub fn fair(&mut self, now: i64, anchor_c: f64, ttc: f64) -> Option<f64> {
        if !(0.0..100.0).contains(&anchor_c) || ttc <= 0.0 { return None; }
        let last=self.at(now)?;
        let prev=self.at(now-1_000_000)?;
        let variance=self.variance(now)?;
        let p=(anchor_c/100.0).clamp(0.001,0.999);
        let z=(p/(1.0-p)).ln()+1.6*(last/prev).ln()/(variance*ttc).sqrt();
        Some(100.0/(1.0+(-z.clamp(-30.0,30.0)).exp()))
    }
}

pub struct Recorder {
    tx: Option<SyncSender<Value>>,
    worker: Option<JoinHandle<Result<()>>>,
}
impl Recorder {
    pub fn new(dir: PathBuf) -> Result<Self> {
        fs::create_dir_all(&dir)?;
        let (tx,rx)=mpsc::sync_channel::<Value>(20_000);
        let worker=thread::spawn(move || -> Result<()> {
            let mut part=0;
            let mut opened=0;
            let mut writer: Option<GzEncoder<BufWriter<File>>>=None;
            let mut current=PathBuf::new();
            for row in rx {
                let now=crate::unix_us();
                if writer.is_none() || now-opened >= 300_000_000 {
                    if let Some(w)=writer.take() {
                        w.finish()?.flush()?;
                        fs::rename(&current,current.with_extension("gz"))?;
                    }
                    let bytes: u64=fs::read_dir(&dir)?.filter_map(|e| e.ok())
                        .filter_map(|e| e.metadata().ok()).map(|m|m.len()).sum();
                    if bytes >= 1_073_741_824 { bail!("audit spool reached 1 GiB; archive before resuming"); }
                    current=dir.join(format!("audit_{now}_{part:05}.jsonl.partial"));
                    writer=Some(GzEncoder::new(BufWriter::new(File::create(&current)?),Compression::new(3)));
                    opened=now; part+=1;
                }
                let w=writer.as_mut().unwrap();
                serde_json::to_writer(&mut *w,&row)?;
                w.write_all(b"\n")?;
            }
            if let Some(w)=writer { w.finish()?.flush()?; fs::rename(&current,current.with_extension("gz"))?; }
            Ok(())
        });
        Ok(Self { tx:Some(tx), worker:Some(worker) })
    }
    pub fn row(&self, row:Value)->Result<()> {
        // A full channel invalidates the run rather than silently dropping evidence.
        self.tx.as_ref().unwrap().try_send(row).map_err(|e|anyhow::anyhow!("audit unavailable: {e}"))
    }
    pub fn finish(mut self)->Result<()> {
        self.tx.take();
        self.worker.take().unwrap().join().map_err(|_|anyhow::anyhow!("audit thread panicked"))?
    }
}
impl Drop for Recorder {
    fn drop(&mut self) {
        self.tx.take();
        if let Some(worker)=self.worker.take() {
            match worker.join() {
                Ok(Ok(()))=>(),
                error=>eprintln!("audit finalization: {error:?}"),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn probability_adjustment_warmup_direction_and_staleness() {
        let mut s=Spot::default();
        for i in 0..61 { s.push(i*1_000_000,60000.0+((i%2) as f64)); }
        assert!(s.fair(60_000_000,50.0,300.0).is_none());
        s.push(61_000_000,60003.0);
        let f=s.fair(61_000_000,50.0,300.0).unwrap();
        assert!(f>50.0 && f<100.0);
        assert!(s.fair(82_000_001,50.0,300.0).is_none());
        assert!(s.fair(61_000_000,50.0,0.0).is_none());
    }
    #[test]
    fn probability_adjustment_accepts_sparse_ticker_updates() {
        let mut s=Spot::default();
        for i in 0..=13 { s.push(i*5_000_000,2700.0+(i%2) as f64); }
        assert!(s.fair(65_000_000,50.0,300.0).is_some());
    }
    #[test]
    fn gap_in_variance_window_fails_closed() {
        let mut s=Spot::default();
        for i in 0..70 { if !(15..40).contains(&i) { s.push(i*1_000_000,60000.0+(i%2) as f64); } }
        assert!(s.fair(69_000_000,50.0,300.0).is_none());
    }
}
