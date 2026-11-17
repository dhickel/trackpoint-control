//! Opt-in release inference. This is not a hardware touch/contact signal.
use crate::motion::{Settings, median};
use std::collections::VecDeque;

#[derive(Default)]
pub(crate) struct Release {
    time: f64,
    active_seconds: f64,
    history: VecDeque<(f64, f64)>,
    candidate: Option<Candidate>,
}

struct Candidate {
    started: f64,
    ceiling: f64,
    samples: Vec<[f64; 2]>,
}

pub(crate) enum Action {
    Continue,
    Hold,
    Calibrate([f64; 2]),
}

impl Release {
    pub(crate) fn step(
        &mut self,
        sample: [f64; 2],
        distance: f64,
        dt: f64,
        buttons: u8,
        cfg: &Settings,
    ) -> Action {
        if buttons != 0 {
            *self = Self::default();
            return Action::Continue;
        }
        self.time += dt;
        if let Some(mut candidate) = self.candidate.take() {
            // Renewed pressure cancels the candidate immediately; a held
            // button never permits an automatic recalibration.
            if distance > candidate.ceiling {
                self.history.clear();
                self.active_seconds = 0.0;
                return Action::Continue;
            }
            if candidate.samples.len() == 128 {
                candidate.samples.remove(0);
            }
            candidate.samples.push(sample);
            if self.time - candidate.started >= cfg.release_settle_ms / 1000.0 {
                let center = median(&candidate.samples);
                let settled = candidate.samples.len() >= 3
                    && candidate
                        .samples
                        .iter()
                        .all(|s| (s[0] - center[0]).hypot(s[1] - center[1]) <= cfg.release_noise);
                *self = Self::default();
                return if settled {
                    Action::Calibrate(center)
                } else {
                    Action::Continue
                };
            }
            self.candidate = Some(candidate);
            return Action::Hold;
        }

        // Only a rapid unload following at least half a second of movement
        // qualifies. A constant hold, including a very slow one, cannot.
        while self
            .history
            .front()
            .is_some_and(|(t, _)| self.time - t > 0.15)
        {
            self.history.pop_front();
        }
        let recent = self.history.iter().map(|(_, d)| *d).fold(0.0, f64::max);
        let ceiling = cfg.release_radius.min(recent * cfg.release_ratio);
        let qualifies = self.active_seconds >= 0.5
            && recent - distance >= cfg.release_drop
            && distance <= ceiling;
        if self.history.len() == 128 {
            self.history.pop_front();
        }
        self.history.push_back((self.time, distance));
        if distance > cfg.deadzone {
            self.active_seconds += dt;
        } else {
            self.active_seconds = 0.0;
        }
        if qualifies {
            self.candidate = Some(Candidate {
                started: self.time,
                ceiling,
                samples: vec![sample],
            });
            Action::Hold
        } else {
            Action::Continue
        }
    }
}
