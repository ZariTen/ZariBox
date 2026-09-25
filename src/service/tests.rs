use super::*;
use crate::engine::LABEL_PROFILE;
use crate::process::Output;
use std::cell::RefCell;
use std::collections::BTreeMap;
use std::rc::Rc;
use std::sync::{Mutex, MutexGuard};

static ENV_LOCK: Mutex<()> = Mutex::new(());

/// Isolated state/data directories; holds a global lock because tests
/// share the process environment.
pub(crate) struct Sandbox {
    pub dir: tempfile::TempDir,
    _guard: MutexGuard<'static, ()>,
}

impl Sandbox {
    pub(crate) fn new() -> Self {
        let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = tempfile::tempdir().unwrap();
        // SAFETY: serialised by ENV_LOCK; no other thread reads these.
        unsafe {
            std::env::set_var("ZARIBOX_STATE_HOME", dir.path().join("state"));
            std::env::set_var("XDG_DATA_HOME", dir.path().join("data"));
            std::env::remove_var("ZARIBOX_ALLOWED_MOUNT_ROOTS");
            std::env::remove_var("ZARIBOX_PROVISION_TIMEOUT");
        }
        Self { dir, _guard: guard }
    }

    pub(crate) fn manifest(&self, file: &str, body: &str) -> PathBuf {
        let path = self.dir.path().join(file);
        std::fs::write(&path, body).unwrap();
        path
    }
}

#[derive(Default)]
pub(crate) struct FakeState {
    pub containers: BTreeMap<String, StringMap>,
    pub calls: Vec<String>,
    pub installed: String,
    pub fail_create: bool,
}

#[derive(Clone, Default)]
pub(crate) struct FakeBackend(pub Rc<RefCell<FakeState>>);

impl Backend for FakeBackend {
    fn runtime_present(&self) -> bool {
        true
    }
    fn container_exists(&self, name: &str) -> Result<bool> {
        Ok(self.0.borrow().containers.contains_key(name))
    }
    fn create(&self, request: &CreateRequest<'_>) -> Result<()> {
        let mut state = self.0.borrow_mut();
        state.calls.push(format!("create {}", request.name));
        ensure!(!state.fail_create, "create failed");
        let mut labels = request.policy.labels.clone();
        labels.insert(LABEL_MANAGED.into(), "true".into());
        labels.insert(LABEL_PROFILE.into(), request.policy.profile.as_str().into());
        state.containers.insert(request.name.into(), labels);
        Ok(())
    }
    fn exec(&self, name: &str, command: &[String], _: &ExecOptions) -> Result<Output> {
        let mut state = self.0.borrow_mut();
        state
            .calls
            .push(format!("exec {name} {}", command.join(" ")));
        Ok(Output {
            stdout: state.installed.clone(),
            ..Output::default()
        })
    }
    fn enter(&self, _: &str) -> Result<i32> {
        Ok(0)
    }
    fn post_install(&self, _: &str, _: &str) -> Result<()> {
        Ok(())
    }
    fn start(&self, name: &str) -> Result<()> {
        self.0.borrow_mut().calls.push(format!("start {name}"));
        Ok(())
    }
    fn stop(&self, name: &str) -> Result<()> {
        self.0.borrow_mut().calls.push(format!("stop {name}"));
        Ok(())
    }
    fn remove(&self, name: &str) -> Result<()> {
        let mut state = self.0.borrow_mut();
        state.calls.push(format!("rm {name}"));
        state.containers.remove(name);
        Ok(())
    }
    fn rename(&self, name: &str, new_name: &str) -> Result<()> {
        let mut state = self.0.borrow_mut();
        state.calls.push(format!("rename {name} {new_name}"));
        let labels = state.containers.remove(name).context("missing")?;
        state.containers.insert(new_name.into(), labels);
        Ok(())
    }
    fn label(&self, name: &str, key: &str) -> Result<Option<String>> {
        Ok(self
            .0
            .borrow()
            .containers
            .get(name)
            .and_then(|l| l.get(key).cloned()))
    }
    fn image_digest(&self, _: &str) -> Result<Option<String>> {
        Ok(Some("sha256:feed".into()))
    }
    fn is_running(&self, _: &str) -> Result<bool> {
        Ok(true)
    }
}

pub(crate) fn service() -> (Service, Rc<RefCell<FakeState>>) {
    let backend = FakeBackend::default();
    let state = Rc::clone(&backend.0);
    (Service::new(Box::new(backend)), state)
}

fn arg(path: &Path) -> Option<&str> {
    path.to_str()
}

#[test]
fn create_then_reconcile_packages() {
    let sandbox = Sandbox::new();
    let path = sandbox.manifest(
        "dev.yaml",
        "Image: archlinux\nPackages: [git]\nRun: [echo hi]\n",
    );
    let (service, fake) = service();

    let plan = service.plan(arg(&path)).unwrap();
    let kinds: Vec<_> = plan.actions.iter().map(|a| a.action.clone()).collect();
    assert!(matches!(kinds[0], Action::Create { .. }));
    assert_eq!(
        kinds[1],
        Action::InstallPackages {
            packages: vec!["git".into()]
        }
    );
    assert_eq!(kinds[2], Action::RunPostInstall { count: 1 });
    let json = serde_json::to_value(&plan.actions[0]).unwrap();
    assert_eq!(json["kind"], "create");
    assert_eq!(
        json["details"]["image"],
        "docker.io/library/archlinux:latest"
    );

    let op = service
        .ensure(arg(&path), EnsureOptions::default())
        .unwrap();
    assert_eq!(op.actions, ["create", "sync_packages", "run_post_install"]);
    assert!(
        fake.borrow()
            .calls
            .iter()
            .any(|c| c.contains("pacman -Syu --noconfirm \"$@\" _ git"))
    );
    assert!(default_home("dev").is_dir());

    // In sync: nothing to do.
    assert!(service.plan(arg(&path)).unwrap().actions.is_empty());
    assert_eq!(service.list().unwrap()[0].name, "dev");
    assert!(service.status("dev").unwrap().config_in_sync);

    // Removing a package is destructive.
    std::fs::write(&path, "Image: archlinux\nRun: [echo hi]\n").unwrap();
    let error = service
        .ensure(arg(&path), EnsureOptions::default())
        .unwrap_err();
    assert!(error.to_string().contains("--force"));
    let force = EnsureOptions {
        force: true,
        ..EnsureOptions::default()
    };
    assert_eq!(
        service.ensure(arg(&path), force).unwrap().actions,
        ["sync_packages"]
    );
    assert!(
        fake.borrow()
            .calls
            .iter()
            .any(|c| c.contains("pacman -Rns --noconfirm \"$@\" _ git"))
    );
    assert!(!service.ensure(arg(&path), force).unwrap().changed);
}

#[test]
fn recreate_keeps_backup_until_success() {
    let sandbox = Sandbox::new();
    let path = sandbox.manifest("dev.yaml", "Image: archlinux\n");
    let (service, fake) = service();
    service
        .ensure(arg(&path), EnsureOptions::default())
        .unwrap();

    std::fs::write(&path, "Image: ubuntu\n").unwrap();
    fake.borrow_mut().fail_create = true;
    let force = EnsureOptions {
        force: true,
        ..EnsureOptions::default()
    };
    assert!(service.ensure(arg(&path), force).is_err());
    {
        let state = fake.borrow();
        assert_eq!(state.containers.keys().collect::<Vec<_>>(), ["dev"]);
        assert!(state.calls.last().unwrap().starts_with("start dev"));
    }

    fake.borrow_mut().fail_create = false;
    let op = service.ensure(arg(&path), force).unwrap();
    assert_eq!(op.actions, ["recreate"]);
    assert_eq!(fake.borrow().containers.keys().collect::<Vec<_>>(), ["dev"]);
}

#[test]
fn refuses_foreign_containers() {
    let sandbox = Sandbox::new();
    let path = sandbox.manifest("dev.yaml", "Image: archlinux\n");
    let (service, fake) = service();
    fake.borrow_mut()
        .containers
        .insert("dev".into(), StringMap::new());
    let force = EnsureOptions {
        force: true,
        ..EnsureOptions::default()
    };
    let error = service.ensure(arg(&path), force).unwrap_err();
    assert!(error.to_string().contains("unmanaged"), "{error}");
    let error = service
        .destroy(path.to_str().unwrap(), DEFAULT_LOCK_TIMEOUT)
        .unwrap_err();
    assert!(error.to_string().contains("unmanaged"));

    fake.borrow_mut().containers.insert(
        "dev".into(),
        StringMap::from([
            (LABEL_MANAGED.into(), "true".into()),
            (LABEL_PROJECT.into(), "other".into()),
        ]),
    );
    let error = service
        .exec(path.to_str().unwrap(), ExecRequest::new(vec!["id".into()]))
        .unwrap_err();
    assert!(error.to_string().contains("another ZariBox project"));
}

#[test]
fn exec_destroy_and_cleanup() {
    let sandbox = Sandbox::new();
    let path = sandbox.manifest(
        "agent.yaml",
        "ApiVersion: zaribox.dev/v1\nKind: AgentBox\nMetadata:\n  TTL: 0\nRuntime:\n  Image: alpine\n  Workdir: /w\n",
    );
    let (service, fake) = service();
    service
        .ensure(arg(&path), EnsureOptions::default())
        .unwrap();
    let result = service
        .exec("agent", ExecRequest::new(vec!["id".into()]))
        .unwrap();
    assert_eq!(result.exit_code, 0);
    assert!(fake.borrow().calls.iter().any(|c| c == "exec agent id"));

    assert_eq!(service.cleanup(), ["agent"]);
    assert!(fake.borrow().containers.is_empty());
    assert!(service.list().unwrap().is_empty());
    let op = service
        .destroy(path.to_str().unwrap(), DEFAULT_LOCK_TIMEOUT)
        .unwrap();
    assert!(!op.changed);
}

#[test]
fn agent_mounts_must_stay_in_roots() {
    let sandbox = Sandbox::new();
    std::fs::create_dir(sandbox.dir.path().join("src")).unwrap();
    let body = |source: &str| {
        format!(
            "ApiVersion: zaribox.dev/v1\nKind: AgentBox\nWorkspace:\n  Mounts:\n    - Source: {source}\n      Target: /w\n      Options: [nodev, nodev]\nRuntime:\n  Image: alpine\n"
        )
    };
    let path = sandbox.manifest("agent.yaml", &body("./src"));
    let manifest = service().0.load(arg(&path)).unwrap();
    let specs = mount_specs(&manifest).unwrap();
    assert_eq!(specs[0].options, "rw,nodev");
    assert!(specs[0].source.ends_with("src"));

    sandbox.manifest("agent.yaml", &body("/etc"));
    let error = service().0.load(arg(&path)).unwrap_err();
    assert!(error.to_string().contains("outside the allowed roots"));
    sandbox.manifest("agent.yaml", &body("./missing"));
    assert!(
        service()
            .0
            .load(arg(&path))
            .unwrap_err()
            .to_string()
            .contains("does not exist")
    );
}

#[test]
fn export_merges_packages() {
    let sandbox = Sandbox::new();
    let path = sandbox.manifest(
        "dev.yaml",
        "Image: archlinux\nPackages:\n  - git\n  # keep\nRun:\n  - echo hi\n",
    );
    let (service, fake) = service();
    service
        .ensure(arg(&path), EnsureOptions::default())
        .unwrap();
    fake.borrow_mut().installed = "git 2.0\nvim 9.1\nbase 3\n".into();
    assert_eq!(service.export("dev").unwrap(), ["base", "vim"]);
    assert_eq!(
        std::fs::read_to_string(&path).unwrap(),
        "Image: archlinux\nPackages:\n  - base\n  - git\n  - vim\nRun:\n  - echo hi\n"
    );
    let status = service.status("dev").unwrap();
    assert!(status.install.is_empty());
    assert_eq!(status.applied_packages, ["base", "git", "vim"]);
}
