use trackpoint_control::motion::{Motion, Settings};

fn enabled() -> Settings {
    Settings {
        release_correction: true,
        idle_radius: 4.0,
        idle_seconds: 1.0,
        drift_tau_seconds: 3.0,
        ..Settings::default()
    }
}

fn hold(motion: &mut Motion, sample: [f64; 2], count: usize) {
    for _ in 0..count {
        motion.step(sample, 0.05, 0);
    }
}

#[test]
fn a_settled_unload_rezeros_residual_force_and_follows_its_slow_tail() {
    let mut motion = Motion::new([0.0; 2], enabled()).unwrap();
    hold(&mut motion, [32.0, -3.0], 400);
    assert_eq!(motion.origin, [0.0; 2]);
    for n in 0..40 {
        let noise = (n % 3) as f64 - 1.0;
        assert_eq!(motion.step([5.0 + noise, 12.0], 0.05, 0), [0; 2]);
    }
    assert_eq!(motion.release_calibrations(), 1);
    assert!((motion.origin[0] - 5.0).abs() < 0.2);
    // A sensor's neutral reading can continue to relax after the finger lifts.
    for n in 0..600 {
        let f = n as f64 / 600.0;
        let noise = (n % 3) as f64 - 1.0;
        assert_eq!(
            motion.step([5.0 + 4.0 * f + noise, 12.0 - 8.0 * f], 0.05, 0),
            [0; 2]
        );
    }
    assert_eq!(motion.release_calibrations(), 1);
}

#[test]
fn five_minutes_of_very_slow_motion_is_not_a_release() {
    let mut motion = Motion::new([0.0; 2], enabled()).unwrap();
    for _ in 0..5 {
        let counts: i32 = (0..1200).map(|_| motion.step([5.0, 0.0], 0.05, 0)[0]).sum();
        assert!((counts - 60).abs() <= 1);
    }
    assert_eq!(motion.origin, [0.0; 2]);
    assert_eq!(motion.release_calibrations(), 0);
}

#[test]
fn gradual_pressure_reduction_preserves_fine_motion() {
    let mut motion = Motion::new([0.0; 2], enabled()).unwrap();
    hold(&mut motion, [32.0, 0.0], 100);
    for n in 0..2000 {
        motion.step([32.0 - 27.0 * n as f64 / 2000.0, 0.0], 0.05, 0);
    }
    let counts: i32 = (0..1200).map(|_| motion.step([5.0, 0.0], 0.05, 0)[0]).sum();
    assert!((counts - 60).abs() <= 1);
    assert_eq!(motion.origin, [0.0; 2]);
    assert_eq!(motion.release_calibrations(), 0);
}

#[test]
fn a_direction_reversal_outside_the_release_region_is_immediate() {
    let mut motion = Motion::new([0.0; 2], enabled()).unwrap();
    hold(&mut motion, [32.0, 0.0], 100);
    assert!(motion.step([-32.0, 0.0], 0.05, 0)[0] < 0);
    assert_eq!(motion.release_calibrations(), 0);
}

#[test]
fn short_flicks_do_not_arm_release_correction() {
    let mut motion = Motion::new([0.0; 2], enabled()).unwrap();
    hold(&mut motion, [32.0, 0.0], 4);
    hold(&mut motion, [8.0, 0.0], 100);
    assert_eq!(motion.release_calibrations(), 0);
    assert_eq!(motion.origin, [0.0; 2]);
}

#[test]
fn unstable_low_force_does_not_recalibrate() {
    let mut motion = Motion::new([0.0; 2], enabled()).unwrap();
    hold(&mut motion, [32.0, 0.0], 100);
    for n in 0..20 {
        motion.step([if n % 2 == 0 { 6.0 } else { 14.0 }, 0.0], 0.05, 0);
    }
    assert_eq!(motion.release_calibrations(), 0);
    assert_eq!(motion.origin, [0.0; 2]);
}

#[test]
fn renewed_pressure_and_buttons_cancel_pending_release() {
    let mut motion = Motion::new([0.0; 2], enabled()).unwrap();
    hold(&mut motion, [32.0, 0.0], 100);
    assert_eq!(motion.step([8.0, 0.0], 0.05, 0), [0; 2]);
    assert!(motion.step([32.0, 0.0], 0.05, 0)[0] > 0);
    hold(&mut motion, [32.0, 0.0], 100);
    assert_eq!(motion.step([8.0, 0.0], 0.05, 0), [0; 2]);
    for _ in 0..40 {
        motion.step([8.0, 0.0], 0.05, 1);
    }
    assert_eq!(motion.origin, [0.0; 2]);
    assert_eq!(motion.release_calibrations(), 0);
}

#[test]
fn a_pause_or_explicit_calibration_cancels_pending_release() {
    let mut motion = Motion::new([0.0; 2], enabled()).unwrap();
    hold(&mut motion, [32.0, 0.0], 100);
    motion.step([8.0, 0.0], 0.05, 0);
    motion.step([8.0, 0.0], 10.0, 0);
    hold(&mut motion, [8.0, 0.0], 40);
    assert_eq!(motion.release_calibrations(), 0);
    hold(&mut motion, [32.0, 0.0], 100);
    motion.step([8.0, 0.0], 0.05, 0);
    motion.calibrate([8.0, 0.0]);
    hold(&mut motion, [8.0, 0.0], 40);
    assert_eq!(motion.origin, [8.0, 0.0]);
    assert_eq!(motion.release_calibrations(), 0);
}

#[test]
fn release_correction_is_opt_in() {
    let mut motion = Motion::new([0.0; 2], Settings::default()).unwrap();
    hold(&mut motion, [32.0, 0.0], 100);
    let counts: i32 = (0..100).map(|_| motion.step([8.0, 0.0], 0.05, 0)[0]).sum();
    assert!(counts > 0);
    assert_eq!(motion.origin, [0.0; 2]);
    assert_eq!(motion.release_calibrations(), 0);
}
