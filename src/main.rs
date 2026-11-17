#[cfg(not(all(
    target_os = "linux",
    any(target_arch = "x86_64", target_arch = "aarch64")
)))]
compile_error!("This version supports 64-bit Linux x86_64 and aarch64 uinput ABIs");

use anyhow::{Context, Result, ensure};
use clap::{Args, Parser, Subcommand};
use serde::Deserialize;
use serde_json::json;
use std::{
    fs::{self, File},
    io::{self, BufRead, Write},
    path::{Path, PathBuf},
    time::Duration,
};
use trackpoint_control::{
    driver, hardware,
    motion::{Config, Motion, Settings},
    sys,
};

#[derive(Parser)]
#[command(version, about = "Elan raw-sensor TrackPoint control (Rust)")]
struct Cli {
    #[command(subcommand)]
    action: Action,
}

#[derive(Args)]
struct Common {
    #[arg(long)]
    config: Option<PathBuf>,
    #[arg(long)]
    record: Option<PathBuf>,
}

#[derive(Subcommand)]
enum Action {
    /// Report stock ports and whether a driver lease exists.
    Status,
    /// Reset the controller and restore psmouse after an abnormal exit.
    Restore {
        /// Wait briefly for an exiting worker's lock; never take a live lock.
        #[arg(long, default_value_t = 0, value_parser = clap::value_parser!(u64).range(0..=10))]
        wait_seconds: u64,
    },
    /// Release the stick, then collect a new zero for about 1.6 seconds.
    Calibrate,
    /// Bounded trial; keep hands off the stick for the first two seconds.
    Trial {
        #[arg(long, default_value_t = 60, value_parser = clap::value_parser!(u64).range(1..=3600))]
        seconds: u64,
        /// Emit pointer movement. Without this flag the trial only observes.
        #[arg(long)]
        enable_pointer: bool,
        #[command(flatten)]
        common: Common,
    },
    /// Run continuously; keep hands off the stick for the first two seconds.
    Run {
        #[command(flatten)]
        common: Common,
    },
    /// Replay JSONL force samples without accessing hardware (development).
    Replay {
        #[arg(long)]
        input: PathBuf,
    },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReplayStart {
    origin: [f64; 2],
    #[serde(default)]
    settings: Settings,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReplayStep {
    sample: [f64; 2],
    dt: f64,
    #[serde(default)]
    buttons: u8,
    calibrate: Option<[f64; 2]>,
}

fn replay(path: &Path) -> Result<()> {
    let mut lines = io::BufReader::new(File::open(path)?).lines();
    let start: ReplayStart =
        serde_json::from_str(&lines.next().context("Missing replay header")??)?;
    let mut motion = Motion::new(start.origin, start.settings)?;
    let mut out = io::BufWriter::new(io::stdout().lock());
    for line in lines {
        let step: ReplayStep = serde_json::from_str(&line?)?;
        if let Some(origin) = step.calibrate {
            ensure!(
                origin.iter().all(|x| x.is_finite()),
                "Non-finite calibration"
            );
            motion.calibrate(origin);
        }
        let delta = motion.step(step.sample, step.dt, step.buttons);
        serde_json::to_writer(
            &mut out,
            &json!({"delta":delta, "origin":motion.origin, "force":motion.filtered,
                "release_calibrations":motion.release_calibrations()}),
        )?;
        writeln!(out)?;
    }
    out.flush()?;
    Ok(())
}

fn main_result() -> Result<i32> {
    let cli = Cli::parse();
    match cli.action {
        Action::Status => {
            driver::log(
                json!({"stock_ports":hardware::ports()?, "lease_present":Path::new(hardware::RUNTIME).join("lease.json").exists(),
                "implementation":"rust", "version":env!("CARGO_PKG_VERSION")}),
            )?;
            return Ok(0);
        }
        Action::Replay { input } => {
            replay(&input)?;
            return Ok(0);
        }
        _ => (),
    }
    ensure!(sys::root(), "This operation requires root; use sudo");
    let (common, seconds, observe) = match cli.action {
        Action::Restore { wait_seconds } => {
            hardware::recover(Duration::from_secs(wait_seconds))?;
            return Ok(0);
        }
        Action::Calibrate => {
            driver::calibrate()?;
            return Ok(0);
        }
        Action::Trial {
            seconds,
            enable_pointer,
            common,
        } => (common, seconds, !enable_pointer),
        Action::Run { common } => (common, 0, false),
        _ => unreachable!(),
    };
    let settings = match common.config {
        Some(path) => {
            toml::from_str::<Config>(
                &fs::read_to_string(&path)
                    .with_context(|| format!("Reading {}", path.display()))?,
            )?
            .motion
        }
        None => Settings::default(),
    };
    driver::run(driver::Options {
        settings,
        duration: Duration::from_secs(seconds),
        observe,
        record: common.record,
    })
}

fn main() {
    let code = match main_result() {
        Ok(code) => code,
        Err(error) => {
            eprintln!("trackpoint-control: {error:#}");
            1
        }
    };
    std::process::exit(code);
}
