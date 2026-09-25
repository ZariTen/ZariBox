//! Subprocess execution with optional timeout and output limits.

use std::io::Read;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use nix::sys::signal::{Signal, killpg};
use nix::unistd::Pid;

/// Exit code reported for commands killed by the timeout (as in `timeout(1)`).
pub const TIMEOUT_EXIT_CODE: i32 = 124;

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Output {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
    pub truncated: bool,
}

impl Output {
    pub fn success(&self) -> bool {
        self.exit_code == 0
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RunOptions {
    /// Capture stdout/stderr instead of inheriting the terminal.
    pub capture: bool,
    pub timeout: Option<Duration>,
    /// Combined stdout+stderr budget; extra output is dropped.
    pub max_output: Option<usize>,
}

impl Default for RunOptions {
    fn default() -> Self {
        Self {
            capture: true,
            timeout: None,
            max_output: None,
        }
    }
}

/// Whether `binary` resolves to an executable file on `$PATH`.
pub fn command_exists(binary: &str) -> bool {
    let executable = |path: &Path| {
        std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
    };
    if binary.contains('/') {
        return executable(Path::new(binary));
    }
    std::env::var_os("PATH").is_some_and(|search| {
        std::env::split_paths(&search).any(|dir| executable(&dir.join(binary)))
    })
}

fn exit_code(status: ExitStatus) -> i32 {
    status
        .code()
        .or_else(|| status.signal().map(|signal| 128 + signal))
        .unwrap_or(1)
}

fn wait_until(child: &mut Child, timeout: Option<Duration>) -> Result<Option<ExitStatus>> {
    let Some(timeout) = timeout else {
        return Ok(Some(child.wait()?));
    };
    let deadline = Instant::now() + timeout;
    let mut delay = Duration::from_micros(500);
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(Some(status));
        }
        let now = Instant::now();
        if now >= deadline {
            return Ok(None);
        }
        thread::sleep(delay.min(deadline - now));
        delay = (delay * 2).min(Duration::from_millis(50));
    }
}

/// Shared output budget for the stdout and stderr readers.
struct Budget {
    remaining: Option<usize>,
    truncated: bool,
}

fn drain(mut stream: impl Read, budget: Arc<Mutex<Budget>>) -> Vec<u8> {
    let mut kept = Vec::new();
    let mut chunk = vec![0u8; 64 * 1024];
    while let Ok(read @ 1..) = stream.read(&mut chunk) {
        let mut budget = budget.lock().expect("budget lock");
        match budget.remaining {
            None => kept.extend_from_slice(&chunk[..read]),
            Some(remaining) => {
                let take = remaining.min(read);
                kept.extend_from_slice(&chunk[..take]);
                budget.remaining = Some(remaining - take);
                budget.truncated |= take != read;
            }
        }
    }
    kept
}

/// Run `args`, honouring the timeout and output budget in `options`.
pub fn run(args: &[String], options: RunOptions) -> Result<Output> {
    let (program, rest) = args.split_first().context("empty command")?;
    let mut command = Command::new(program);
    command.args(rest);
    if options.capture {
        // Non-interactive: never let a background process group touch the tty.
        command
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0);
    }
    let mut child = command
        .spawn()
        .with_context(|| format!("failed to run {program}"))?;

    let budget = Arc::new(Mutex::new(Budget {
        remaining: options.max_output,
        truncated: false,
    }));
    let readers = options.capture.then(|| {
        let stdout = child.stdout.take().expect("piped stdout");
        let stderr = child.stderr.take().expect("piped stderr");
        let (out_budget, err_budget) = (Arc::clone(&budget), Arc::clone(&budget));
        (
            thread::spawn(move || drain(stdout, out_budget)),
            thread::spawn(move || drain(stderr, err_budget)),
        )
    });

    let (exit_code, timed_out) = match wait_until(&mut child, options.timeout)? {
        Some(status) => (exit_code(status), false),
        None => {
            if options.capture {
                let _ = killpg(Pid::from_raw(child.id() as i32), Signal::SIGKILL);
            } else {
                let _ = child.kill();
            }
            let _ = child.wait();
            (TIMEOUT_EXIT_CODE, true)
        }
    };
    let (stdout, stderr) = match readers {
        Some((out, err)) => (
            out.join().unwrap_or_default(),
            err.join().unwrap_or_default(),
        ),
        None => (Vec::new(), Vec::new()),
    };
    let truncated = budget.lock().expect("budget lock").truncated;
    Ok(Output {
        exit_code,
        stdout: String::from_utf8_lossy(&stdout).into_owned(),
        stderr: String::from_utf8_lossy(&stderr).into_owned(),
        timed_out,
        truncated,
    })
}

#[cfg(test)]
mod tests;
