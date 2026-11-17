use trackpoint_control::hardware::State;

#[test]
fn accepts_python_lease_and_rejects_paths_or_unknown_attributes() {
    let text = r#"{"port":"serio1","physical_path":"/sys/devices/platform/i8042/serio1","settings":{"sensitivity":"128","bind_mode":"auto","rate":"100"}}"#;
    let state: State = serde_json::from_str(text).unwrap();
    state.validate().unwrap();
    for port in ["../serio0", "serio", "/serio1", "serio1/input"] {
        let mut changed = state.clone();
        changed.port = port.into();
        assert!(changed.validate().is_err());
    }
    let mut changed = state.clone();
    changed
        .settings
        .insert("../../drvctl".into(), "rescan".into());
    assert!(changed.validate().is_err());
    changed = state;
    changed
        .settings
        .insert("sensitivity".into(), "128\n0".into());
    assert!(changed.validate().is_err());
}
