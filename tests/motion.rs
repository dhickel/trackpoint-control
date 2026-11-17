use trackpoint_control::motion::{Config, Motion, Settings, median};
use trackpoint_control::uinput::events;

#[test]
fn five_minutes_of_slow_force_never_becomes_zero() {
    let mut motion = Motion::new(
        [100.0, 200.0],
        Settings {
            smoothing_ms: 0.0,
            ..Settings::default()
        },
    )
    .unwrap();
    for _ in 0..5 {
        let counts: i32 = (0..1500)
            .map(|_| motion.step([105.0, 200.0], 0.04, 0)[0])
            .sum();
        assert!((counts - 60).abs() <= 1, "{counts}");
    }
    assert_eq!(motion.origin, [100.0, 200.0]);
}

#[test]
fn stationary_noise_emits_nothing() {
    let mut motion = Motion::new([0.0; 2], Settings::default()).unwrap();
    for n in 0..10000 {
        assert_eq!(
            motion.step([(n as f64).sin(), (n as f64 * 0.7).cos()], 0.04, 0),
            [0; 2]
        );
    }
}

#[test]
fn thermal_tracking_is_confined_to_idle_region_and_stops_with_buttons() {
    let mut motion = Motion::new(
        [0.0; 2],
        Settings {
            smoothing_ms: 0.0,
            ..Settings::default()
        },
    )
    .unwrap();
    for _ in 0..15000 {
        assert_eq!(motion.step([0.5, 0.0], 0.04, 0), [0; 2]);
    }
    assert!(motion.origin[0] > 0.49);
    let original = motion.origin;
    for _ in 0..1000 {
        motion.step([4.0, 0.0], 0.04, 0);
    }
    assert_eq!(motion.origin, original);
    for _ in 0..1000 {
        motion.step([0.0; 2], 0.04, 1);
    }
    assert_eq!(motion.origin, original);
}

#[test]
fn release_never_reverses_motion() {
    let mut motion = Motion::new([20.0, 30.0], Settings::default()).unwrap();
    for _ in 0..500 {
        motion.step([30.0, 30.0], 0.04, 0);
    }
    for i in 0..100 {
        let x = motion.step([20.0, 30.0], 0.04, 0)[0];
        assert!(x >= 0);
        if i >= 3 {
            assert_eq!(x, 0);
        }
    }
}

#[test]
fn pause_and_nonfinite_input_do_not_jump() {
    let mut motion = Motion::new([0.0; 2], Settings::default()).unwrap();
    for dt in [10.0, 0.0, -0.1, f64::NAN] {
        assert_eq!(motion.step([30.0, 0.0], dt, 0), [0; 2]);
    }
    assert_eq!(motion.step([f64::NAN, 1.0], 0.04, 0), [0; 2]);
}

#[test]
fn calibration_clears_previous_force_and_fractional_motion() {
    let mut motion = Motion::new(
        [0.0; 2],
        Settings {
            smoothing_ms: 0.0,
            ..Settings::default()
        },
    )
    .unwrap();
    let y: i32 = (0..10).map(|_| motion.step([0.0, 10.0], 0.04, 0)[1]).sum();
    assert!(y < 0);
    motion.calibrate([0.0, 10.0]);
    assert_eq!(motion.step([0.0, 10.0], 0.04, 0), [0; 2]);
    assert_eq!(
        median(&[[5.0, 3.0], [1.0, 2.0], [3.0, 4.0], [9.0, 0.0]]),
        [4.0, 2.5]
    );
}

#[test]
fn configuration_rejects_typos_types_nonfinite_and_out_of_range_values() {
    for text in [
        "[motion]\ngain=true",
        "[motion]\ninvert_x=1",
        "[motion]\ngian=1",
        "[motion]\nrelease_correction=1",
        "[unrecognized]\nx=1",
    ] {
        assert!(toml::from_str::<Config>(text).is_err(), "{text}");
    }
    for text in [
        "[motion]\ngain=nan",
        "[motion]\ndeadzone=-1",
        "[motion]\nidle_radius=5",
        "[motion]\ncurve=0",
        "[motion]\nrelease_radius=nan",
        "[motion]\nrelease_drop=0",
        "[motion]\nrelease_ratio=1",
        "[motion]\nrelease_settle_ms=0",
        "[motion]\nrelease_noise=0",
    ] {
        let cfg: Config = toml::from_str(text).unwrap();
        assert!(cfg.motion.validate().is_err(), "{text}");
    }
    let cfg: Config = toml::from_str(include_str!("../config.example.toml")).unwrap();
    assert_eq!(
        serde_json::to_value(&cfg.motion).unwrap(),
        serde_json::to_value(Settings::default()).unwrap()
    );
}

#[test]
fn button_edges_and_axis_events_share_one_sync_frame() {
    assert_eq!(
        events(0, 5, [3, -2]),
        vec![
            (1, 0x110, 1),
            (1, 0x112, 1),
            (2, 0, 3),
            (2, 1, -2),
            (0, 0, 0)
        ]
    );
    assert!(events(5, 5, [0, 0]).is_empty());
    assert_eq!(
        events(5, 0, [0, 0]),
        vec![(1, 0x110, 0), (1, 0x112, 0), (0, 0, 0)]
    );
    assert_eq!(events(0, 2, [0, 0]), vec![(1, 0x111, 1), (0, 0, 0)]);
}
