use super::*;
use std::os::unix::net::UnixListener;
use std::rc::Rc;

fn v(items: &[&str]) -> Vec<String> {
    items.iter().map(|x| x.to_string()).collect()
}

type Log = Rc<RefCell<Vec<Vec<String>>>>;

struct Harness {
    backend: PodmanBackend,
    log: Log,
    interactive: Log,
    _dir: tempfile::TempDir,
    root: PathBuf,
}

impl Harness {
    fn new(env: &[(&str, &str)], respond: impl Fn(&[String]) -> Output + 'static) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let root = std::fs::canonicalize(dir.path()).unwrap();
        let log: Log = Rc::default();
        let interactive: Log = Rc::default();
        let (runner_log, interactive_log) = (Rc::clone(&log), Rc::clone(&interactive));
        let mut vars: HashMap<String, String> = HashMap::from([
            (s("USER"), s("tester")),
            (s("HOME"), root.join("host-home").display().to_string()),
            (
                s("XDG_CONFIG_HOME"),
                root.join("config").display().to_string(),
            ),
            (
                s("XDG_RUNTIME_DIR"),
                root.join("missing").display().to_string(),
            ),
        ]);
        for (key, value) in env {
            vars.insert(
                s(*key),
                value.replace("{root}", &root.display().to_string()),
            );
        }
        let backend = PodmanBackend::with_hooks(
            Box::new(move |args, _| {
                runner_log.borrow_mut().push(args.to_vec());
                Ok(respond(args))
            }),
            Box::new(move |args| {
                interactive_log.borrow_mut().push(args.to_vec());
                Ok(0)
            }),
            Box::new(vars),
            root.join("x11"),
        );
        Self {
            backend,
            log,
            interactive,
            _dir: dir,
            root,
        }
    }

    fn path(&self, rel: &str) -> PathBuf {
        self.root.join(rel)
    }

    fn find(&self, prefix: &[&str]) -> Vec<String> {
        self.log
            .borrow()
            .iter()
            .find(|c| c.starts_with(&v(prefix)))
            .cloned()
            .expect("command recorded")
    }
}

fn ok(_: &[String]) -> Output {
    Output::default()
}

fn volumes(args: &[String]) -> Vec<String> {
    args.windows(2)
        .filter(|w| w[0] == "--volume")
        .map(|w| w[1].clone())
        .collect()
}

fn after(args: &[String], flag: &str) -> String {
    args[args.iter().position(|a| a == flag).unwrap() + 1].clone()
}

#[test]
fn exec_command_ordering() {
    let env = StringMap::from([(s("FOO"), s("bar"))]);
    let command = v(&["sh", "-c", "echo hi"]);
    let args = ExecCommand {
        name: "box",
        command: &command,
        user_args: v(&["--user", "1000:1000"]),
        workdir: Some("/work"),
        env: Some(&env),
        graphics_env: v(&["--env", "DISPLAY=:0"]),
        interactive: true,
    }
    .build()
    .unwrap();
    assert_eq!(
        args,
        v(&[
            "podman",
            "exec",
            "-it",
            "--user",
            "1000:1000",
            "--workdir",
            "/work",
            "--env",
            "FOO=bar",
            "--env",
            "DISPLAY=:0",
            "box",
            "sh",
            "-c",
            "echo hi"
        ])
    );
    let relative = ExecCommand {
        name: "box",
        command: &command,
        workdir: Some("rel"),
        ..ExecCommand::default()
    };
    assert!(relative.build().is_err());
}

#[test]
fn login_shell_falls_back() {
    assert!(login_shell_command("zsh").starts_with("if command -v zsh >/dev/null 2>&1"));
    assert!(login_shell_command("evil; rm -rf /").contains("command -v 'evil; rm -rf /' "));
    let script = login_shell_command("zaribox-missing-shell").replace(" -l", " -c 'exit 7'");
    let status = std::process::Command::new("sh")
        .args(["-c", &script])
        .status()
        .unwrap();
    assert_eq!(status.code(), Some(7));
}

#[test]
fn user_setup_script_is_valid_shell() {
    let user = HostUser {
        uid: 1000,
        gid: 1000,
        name: s("zari"),
    };
    let script = user_setup_script(&user, "/home/zari box", true);
    assert!(script.contains("useradd -M -d '/home/zari box' -u 1000 -g 1000 zari"));
    assert!(script.contains("NOPASSWD"));
    assert!(!user_setup_script(&user, "/h", false).contains("sudoers"));
    let status = std::process::Command::new("sh")
        .args(["-n", "-c", &script])
        .status()
        .unwrap();
    assert!(status.success());
}

#[test]
fn desktop_create_mounts_home_and_x11() {
    let h = Harness::new(&[("DISPLAY", ":0"), ("XAUTHORITY", "{root}/cookie")], ok);
    std::fs::write(h.path("cookie"), "cookie").unwrap();
    std::fs::create_dir_all(h.path("x11")).unwrap();
    let home = h.path("box-home").display().to_string();
    h.backend
        .create(&CreateRequest {
            name: "box",
            image: "archlinux",
            home_dir: &home,
            home_mount: true,
            policy: &CreatePolicy {
                extra_flags: v(&["--device", "/dev/fuse"]),
                ..CreatePolicy::default()
            },
        })
        .unwrap();
    let create = h.find(&["podman", "create"]);
    let mounts = volumes(&create);
    let host_home = h.path("host-home").display().to_string();
    let x11 = h.path("x11").display().to_string();
    let xauth = h.path("config/zaribox/box/xauth");
    assert!(mounts.contains(&format!("{host_home}:{host_home}:rw")));
    assert!(mounts.contains(&format!("{home}:{home}:rslave")));
    assert!(mounts.contains(&format!("{x11}:{x11}:ro,rslave")));
    assert!(mounts.contains(&format!("{}:{CONTAINER_XAUTHORITY}:ro", xauth.display())));
    assert_eq!(std::fs::read_to_string(xauth).unwrap(), "cookie");
    assert_eq!(after(&create, "--network"), "host");
    assert_eq!(
        &create[create.len() - 5..],
        &v(&["--device", "/dev/fuse", "archlinux", "sleep", "infinity"])[..]
    );
    let setup = h
        .log
        .borrow()
        .iter()
        .find(|c| c.last().unwrap().contains("getent passwd"))
        .cloned()
        .unwrap();
    assert!(setup.last().unwrap().contains("NOPASSWD"));
}

#[test]
fn agent_create_is_isolated() {
    let h = Harness::new(&[("DISPLAY", ":0")], ok);
    let policy = CreatePolicy {
        profile: Profile::Agent,
        network: Network::None,
        mounts: vec![MountSpec {
            source: PathBuf::from("/src"),
            target: s("/workspace"),
            options: s("ro"),
        }],
        env: StringMap::from([(s("CI"), s("true"))]),
        workdir: Some(s("/workspace")),
        resources: Resources {
            cpus: Some(1.5),
            memory: Some(s("512m")),
            pids_limit: Some(128),
        },
        read_only_root: true,
        writable_tmpfs: v(&["/tmp:rw,size=64m"]),
        ..CreatePolicy::default()
    };
    let home = h.path("home").display().to_string();
    h.backend
        .create(&CreateRequest {
            name: "agent",
            image: "img",
            home_dir: &home,
            home_mount: false,
            policy: &policy,
        })
        .unwrap();
    let create = h.find(&["podman", "create"]);
    assert_eq!(after(&create, "--network"), "none");
    assert_eq!(after(&create, "--ipc"), "private");
    assert_eq!(after(&create, "--cap-drop"), "all");
    assert_eq!(after(&create, "--cpus"), "1.5");
    assert_eq!(after(&create, "--pids-limit"), "128");
    assert_eq!(after(&create, "--workdir"), "/workspace");
    for expected in [
        "no-new-privileges",
        "--read-only",
        "/src:/workspace:ro",
        "CI=true",
    ] {
        assert!(create.contains(&s(expected)), "{expected}");
    }
    assert!(
        !create
            .iter()
            .any(|a| a.contains("xauth") || a.starts_with("DISPLAY"))
    );
    assert!(
        !h.log
            .borrow()
            .iter()
            .any(|c| c.last().unwrap().contains("getent passwd"))
    );
}

#[test]
fn agent_policy_rejects_escape_hatches() {
    let agent = CreatePolicy {
        profile: Profile::Agent,
        network: Network::None,
        ..CreatePolicy::default()
    };
    let flags = CreatePolicy {
        extra_flags: v(&["--privileged"]),
        ..agent.clone()
    };
    assert!(flags.validate().is_err());
    let host = CreatePolicy {
        network: Network::Host,
        ..agent
    };
    assert!(host.validate().is_err());
    let labels = CreatePolicy {
        labels: StringMap::from([(s(LABEL_MANAGED), s("x"))]),
        ..CreatePolicy::default()
    };
    assert!(labels.validate().is_err());
}

#[test]
fn start_refreshes_xauthority_and_reports_failures() {
    let h = Harness::new(&[("XAUTHORITY", "{root}/cookie")], |args| {
        if args.starts_with(&v(&["podman", "start"])) {
            Output {
                exit_code: 125,
                stderr: s("crun: boom\n"),
                ..Output::default()
            }
        } else {
            Output::default()
        }
    });
    let persisted = h.path("config/zaribox/box/xauth");
    std::fs::create_dir_all(persisted.parent().unwrap()).unwrap();
    std::fs::write(&persisted, "old").unwrap();
    std::fs::write(h.path("cookie"), "new").unwrap();
    let error = h.backend.start_with("box", None).unwrap_err();
    assert_eq!(error.to_string(), "podman start failed: crun: boom");
    assert_eq!(std::fs::read_to_string(persisted).unwrap(), "new");
}

#[test]
fn exec_forwards_display_sockets() {
    let h = Harness::new(&[("XDG_RUNTIME_DIR", "{root}/runtime")], ok);
    std::fs::create_dir_all(h.path("runtime")).unwrap();
    std::fs::create_dir_all(h.path("x11")).unwrap();
    let _wayland = UnixListener::bind(h.path("runtime/wayland-1")).unwrap();
    let _x11 = UnixListener::bind(h.path("x11/X0")).unwrap();
    h.backend
        .exec("box", &v(&["true"]), &ExecOptions::default())
        .unwrap();
    let exec = h
        .log
        .borrow()
        .iter()
        .find(|c| c.starts_with(&v(&["podman", "exec"])) && c.last().unwrap() == "true")
        .cloned()
        .unwrap();
    for expected in [
        s("DISPLAY=:0"),
        s("WAYLAND_DISPLAY=wayland-1"),
        s("XDG_SESSION_TYPE=wayland"),
        format!("XDG_RUNTIME_DIR={}", h.path("runtime").display()),
    ] {
        assert!(exec.contains(&expected), "{expected} missing from {exec:?}");
    }

    let agent = ExecOptions {
        agent: Some(true),
        ..ExecOptions::default()
    };
    h.backend.exec("agent", &v(&["id"]), &agent).unwrap();
    let exec = h.find(&["podman", "exec", "--user", "0", "agent", "id"]);
    assert_eq!(exec.len(), 6);
}

#[test]
fn enter_translates_the_working_directory() {
    let cwd = std::fs::canonicalize(std::env::current_dir().unwrap()).unwrap();
    let parent = cwd.parent().unwrap().display().to_string();
    let h = Harness::new(&[], move |args| {
        let stdout = if args[1] == "inspect" && args[3].contains(".Mounts") {
            format!("{parent}\t/run/host/p\n")
        } else if args[1] == "inspect" {
            s("/home/box")
        } else {
            String::new()
        };
        Output {
            stdout,
            ..Output::default()
        }
    });
    assert_eq!(h.backend.enter("box").unwrap(), 0);
    let args = h.interactive.borrow()[0].clone();
    assert_eq!(&args[..3], &v(&["podman", "exec", "-it"])[..]);
    assert_eq!(
        after(&args, "--workdir"),
        format!("/run/host/p/{}", cwd.file_name().unwrap().to_string_lossy())
    );
    assert!(args.contains(&s("HOME=/home/box")));
}

#[test]
fn mounted_workdir_prefers_deepest_mount() {
    let mounts = vec![
        (PathBuf::from("/"), PathBuf::from("/host")),
        (PathBuf::from("/usr"), PathBuf::from("/u")),
    ];
    assert_eq!(
        mounted_workdir(Path::new("/usr/lib"), &mounts).as_deref(),
        Some("/u/lib")
    );
    assert_eq!(
        mounted_workdir(Path::new("/usr"), &mounts).as_deref(),
        Some("/u")
    );
    assert_eq!(
        mounted_workdir(Path::new("/etc"), &mounts).as_deref(),
        Some("/host/etc")
    );
}
