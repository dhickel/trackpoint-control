//! The same force-to-velocity calculation and defaults as the tested Python driver.
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub deadzone: f64,
    pub gain: f64,
    pub curve: f64,
    pub smoothing_ms: f64,
    pub idle_radius: f64,
    pub idle_seconds: f64,
    pub drift_tau_seconds: f64,
    pub max_speed: f64,
    pub invert_x: bool,
    pub invert_y: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            deadzone: 4.0,
            gain: 1.0,
            curve: 1.2,
            smoothing_ms: 5.0,
            idle_radius: 2.0,
            idle_seconds: 5.0,
            drift_tau_seconds: 30.0,
            max_speed: 1800.0,
            invert_x: false,
            invert_y: true,
        }
    }
}

impl Settings {
    pub fn validate(&self) -> Result<()> {
        for (name, value, min, max) in [
            ("deadzone", self.deadzone, 0.0, 100.0),
            ("gain", self.gain, 0.01, 500.0),
            ("curve", self.curve, 1.0, 3.0),
            ("smoothing_ms", self.smoothing_ms, 0.0, 1000.0),
            ("idle_radius", self.idle_radius, 0.0, 100.0),
            ("idle_seconds", self.idle_seconds, 1.0, 3600.0),
            ("drift_tau_seconds", self.drift_tau_seconds, 1.0, 86400.0),
            ("max_speed", self.max_speed, 1.0, 10000.0),
        ] {
            ensure!(
                value.is_finite() && (min..=max).contains(&value),
                "{name} must be between {min} and {max}"
            );
        }
        ensure!(
            self.idle_radius <= self.deadzone,
            "idle_radius must not exceed deadzone"
        );
        Ok(())
    }
}

#[derive(Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    pub motion: Settings,
}

pub struct Motion {
    pub origin: [f64; 2],
    pub filtered: [f64; 2],
    remainder: [f64; 2],
    idle_elapsed: f64,
    settings: Settings,
}

impl Motion {
    pub fn new(origin: [f64; 2], settings: Settings) -> Result<Self> {
        settings.validate()?;
        ensure!(
            origin.iter().all(|x| x.is_finite()),
            "Non-finite calibration"
        );
        Ok(Self {
            origin,
            filtered: [0.0; 2],
            remainder: [0.0; 2],
            idle_elapsed: 0.0,
            settings,
        })
    }

    pub fn calibrate(&mut self, origin: [f64; 2]) {
        self.origin = origin;
        self.filtered = [0.0; 2];
        self.remainder = [0.0; 2];
        self.idle_elapsed = 0.0;
    }

    pub fn step(&mut self, sample: [f64; 2], dt: f64, buttons: u8) -> [i32; 2] {
        if !(dt > 0.0 && dt <= 0.25) || !sample.iter().all(|x| x.is_finite()) {
            self.remainder = [0.0; 2];
            self.filtered = [0.0; 2];
            self.idle_elapsed = 0.0;
            return [0; 2];
        }
        let cfg = &self.settings;
        let alpha = if cfg.smoothing_ms == 0.0 {
            1.0
        } else {
            -(-dt / (cfg.smoothing_ms / 1000.0)).exp_m1()
        };
        for (i, value) in sample.iter().enumerate() {
            let force = value - self.origin[i];
            self.filtered[i] += alpha * (force - self.filtered[i]);
        }
        let distance = self.filtered[0].hypot(self.filtered[1]);
        if cfg.idle_radius > 0.0 && distance <= cfg.idle_radius && buttons == 0 {
            self.idle_elapsed += dt;
            if self.idle_elapsed >= cfg.idle_seconds {
                let beta = -(-dt / cfg.drift_tau_seconds).exp_m1();
                for i in 0..2 {
                    let change = beta * self.filtered[i];
                    self.origin[i] += change;
                    self.filtered[i] -= change;
                }
            }
        } else {
            self.idle_elapsed = 0.0;
        }
        if distance <= cfg.deadzone {
            self.remainder = [0.0; 2];
            return [0; 2];
        }
        let speed = cfg
            .max_speed
            .min(cfg.gain * (distance - cfg.deadzone).powf(cfg.curve));
        let mut out = [0; 2];
        for (i, invert) in [cfg.invert_x, cfg.invert_y].iter().enumerate() {
            let delta = self.filtered[i] / distance * speed * dt * if *invert { -1.0 } else { 1.0 };
            self.remainder[i] += delta;
            out[i] = self.remainder[i].trunc() as i32;
            self.remainder[i] -= f64::from(out[i]);
        }
        out
    }
}

pub fn median(samples: &[[f64; 2]]) -> [f64; 2] {
    assert!(!samples.is_empty());
    std::array::from_fn(|i| {
        let mut values: Vec<_> = samples.iter().map(|s| s[i]).collect();
        values.sort_by(f64::total_cmp);
        let n = values.len();
        if n % 2 == 0 {
            (values[n / 2 - 1] + values[n / 2]) / 2.0
        } else {
            values[n / 2]
        }
    })
}
