use super::*;

fn sh(script: &str) -> Vec<String> {
    vec!["sh".into(), "-c".into(), script.into()]
}

#[test]
fn output_budget_truncates() {
    let options = RunOptions {
        max_output: Some(64),
        ..RunOptions::default()
    };
    let output = run(&sh("printf '%01000d' 0"), options).unwrap();
    assert!(output.success());
    assert_eq!(output.stdout.len(), 64);
    assert!(output.truncated);
}

#[test]
fn timeout_kills_process_group() {
    let started = Instant::now();
    let options = RunOptions {
        timeout: Some(Duration::from_millis(50)),
        ..RunOptions::default()
    };
    let output = run(&sh("sleep 10 & sleep 10"), options).unwrap();
    assert!(started.elapsed() < Duration::from_secs(2));
    assert_eq!(output.exit_code, TIMEOUT_EXIT_CODE);
    assert!(output.timed_out);
}

#[test]
fn captures_streams_and_exit_code() {
    let output = run(&sh("echo out; echo err >&2; exit 3"), RunOptions::default()).unwrap();
    assert_eq!(output.exit_code, 3);
    assert_eq!(output.stdout, "out\n");
    assert_eq!(output.stderr, "err\n");
}

#[test]
fn missing_binary_is_an_error() {
    let error = run(&["zaribox-missing-binary".into()], RunOptions::default()).unwrap_err();
    assert!(format!("{error:#}").starts_with("failed to run zaribox-missing-binary"));
    assert!(command_exists("sh"));
    assert!(!command_exists("zaribox-missing-binary"));
}
