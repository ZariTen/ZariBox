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

#[test]
fn human_summaries() {
    use crate::config::Profile;
    use crate::service::PlannedAction;
    use std::path::PathBuf;

    let validation = Validation {
        valid: true,
        config_path: PathBuf::from("/tmp/archbox.yaml"),
        name: "archbox".into(),
        image: "docker.io/library/archlinux:latest".into(),
        kind: "DesktopBox",
        security_profile: Profile::Default,
    };
    assert_eq!(
        render_validation(&validation),
        "\
archbox  valid
  kind     DesktopBox
  image    docker.io/library/archlinux:latest
  profile  default
  config   /tmp/archbox.yaml"
    );

    let plan = Plan {
        project_id: "p".into(),
        container: "archbox".into(),
        config_path: PathBuf::from("/tmp/archbox.yaml"),
        actions: vec![
            PlannedAction {
                action: Action::Create {
                    image: "docker.io/library/archlinux:latest".into(),
                },
                destructive: false,
            },
            PlannedAction {
                action: Action::RemovePackages {
                    packages: vec!["vim".into()],
                },
                destructive: true,
            },
        ],
        requires_force: true,
        identity_digest: "abc".into(),
    };
    assert_eq!(
        render_plan(&plan),
        "\
archbox  /tmp/archbox.yaml
  create        docker.io/library/archlinux:latest
  remove        vim  (destructive)

Destructive actions need --force."
    );

    let status = Status {
        project_id: "p".into(),
        container: "archbox".into(),
        config_path: PathBuf::from("/tmp/archbox.yaml"),
        exists: true,
        config_in_sync: true,
        desired_packages: vec!["git".into()],
        applied_packages: vec!["git".into()],
        install: Vec::new(),
        remove: Vec::new(),
        image: "docker.io/library/archlinux:latest".into(),
        image_digest: Some(
            "sha256:f3691b4dde62ba4c4b6f0ae2c1fbf28e8c0c8c4b9a35c7e06dc1f70e21aa29f6".into(),
        ),
        security_profile: Profile::Default,
        expires_at: None,
    };
    assert_eq!(
        render_status(&status),
        "\
archbox
  exists    yes
  in sync   yes
  image     docker.io/library/archlinux:latest
  digest    sha256:f3691b4dde62...
  profile   default
  packages  git
  install   none
  remove    none
  path      /tmp/archbox.yaml"
    );

    let listed = vec![BoxSummary {
        project_id: "p".into(),
        name: "archbox".into(),
        config_path: PathBuf::from("/tmp/archbox.yaml"),
        exists: true,
        running: true,
        image: "docker.io/library/archlinux:latest".into(),
        image_digest: None,
        security_profile: Profile::Default,
        expires_at: None,
    }];
    assert_eq!(
        render_list(&listed),
        "\
archbox  running
  image    docker.io/library/archlinux:latest
  profile  default
  config   /tmp/archbox.yaml"
    );
    assert_eq!(render_list(&[]), "No managed boxes.");

    let created = Operation {
        operation_id: "secret".into(),
        changed: true,
        container: "archbox".into(),
        actions: vec!["create", "sync_packages", "run_post_install"],
        warnings: Vec::new(),
    };
    assert_eq!(
        operation_summary("create", &created),
        "'archbox': created the container, synced packages, ran post-install commands"
    );
    assert!(!operation_summary("create", &created).contains("secret"));
    let idle = Operation {
        changed: false,
        actions: Vec::new(),
        ..created
    };
    assert_eq!(
        operation_summary("create", &idle),
        "'archbox' is already up to date"
    );
    assert_eq!(
        operation_summary("remove", &idle),
        "'archbox' was already gone"
    );

    assert_eq!(render_export(&[]), "No new packages to add.");
    assert_eq!(
        render_export(&["git".into()]),
        "Added 1 package to the manifest:\n  git"
    );
    assert_eq!(render_cleanup(&[]), "Nothing to clean up.");
    assert_eq!(render_cleanup(&["oldbox".into()]), "Removed 1:\n  oldbox");

    assert_eq!(
        format_expiry_at(1_700_000_000.0, 1_800_000_000.0),
        "2023-11-14T22:13:20Z (expired)"
    );
    assert_eq!(
        format_expiry_at(1_800_000_000.0, 1_700_000_000.0),
        "2027-01-15T08:00:00Z"
    );
    assert_eq!(
        short_digest("sha256:f3691b4dde62ba4c"),
        "sha256:f3691b4dde62..."
    );

    let exec = ExecResult {
        container: "archbox".into(),
        argv: vec!["true".into()],
        exit_code: 1,
        stdout: String::new(),
        stderr: String::new(),
        timed_out: false,
        truncated: true,
    };
    assert_eq!(exec_notes(&exec), ["output truncated", "command exited 1"]);
}
