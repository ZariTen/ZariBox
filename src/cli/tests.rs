use super::*;

fn parse(args: &[&str]) -> Result<Cli, clap::Error> {
    Cli::try_parse_from(std::iter::once("zaribox").chain(args.iter().copied()))
}

#[test]
fn cli_definition_is_consistent() {
    Cli::command().debug_assert();
}

#[test]
fn exec_arguments() {
    let cli = parse(&[
        "exec",
        "box",
        "--timeout",
        "2.5",
        "--env",
        "A=b=c",
        "--json",
        "--",
        "ls",
        "--json",
    ])
    .unwrap();
    assert!(cli.json);
    let Some(Command::Exec(args)) = cli.command else {
        panic!()
    };
    assert_eq!(args.timeout, Duration::from_millis(2500));
    assert_eq!(args.env, [("A".to_string(), "b=c".to_string())]);
    assert_eq!(args.argv, ["ls", "--json"]);

    assert!(parse(&["exec", "box", "--shell", "ls", "--", "ls"]).is_err());
    assert!(parse(&["exec", "box", "--env", "=x"]).is_err());
    assert!(parse(&["exec", "box", "--timeout", "-1"]).is_err());
    assert!(parse(&["exec", "box", "--max-output-bytes", "-1"]).is_err());
}

#[test]
fn globals_anywhere() {
    let cli = parse(&["--no-color", "create", "box.yaml", "--force", "--json"]).unwrap();
    assert!(cli.json && cli.no_color);
    let Some(Command::Create {
        force,
        lock_timeout,
        ..
    }) = cli.command
    else {
        panic!()
    };
    assert!(force);
    assert_eq!(lock_timeout, Duration::from_secs(30));
    assert!(parse(&[]).unwrap().command.is_none());
}

#[test]
fn envelope_shape() {
    let text = envelope("list", json!([]), None);
    assert_eq!(
        text,
        r#"{"schema_version":1,"ok":true,"command":"list","data":[],"error":null}"#
    );
    let error: Value = serde_json::from_str(&envelope(
        "x",
        Value::Null,
        Some(("operation_failed", "boom".into())),
    ))
    .unwrap();
    assert_eq!(error["ok"], false);
    assert_eq!(error["error"]["message"], "boom");
}
