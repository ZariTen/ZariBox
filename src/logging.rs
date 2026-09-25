//! Human-oriented log lines (`[zaribox] ...`, `  error ...`).

use std::io::{IsTerminal, Write};
use std::sync::atomic::{AtomicBool, Ordering};

const RED: &str = "\x1b[0;31m";
const GRN: &str = "\x1b[0;32m";
const YLW: &str = "\x1b[0;33m";
const BLU: &str = "\x1b[0;34m";
const BOLD: &str = "\x1b[1m";
const RST: &str = "\x1b[0m";

static COLOR_ENABLED: AtomicBool = AtomicBool::new(true);

pub fn set_color_enabled(enabled: bool) {
    COLOR_ENABLED.store(enabled, Ordering::Relaxed);
}

fn format(color: &str, label: &str, message: &str, tty: bool) -> String {
    if COLOR_ENABLED.load(Ordering::Relaxed) && tty {
        format!("{color}{BOLD}{label}{RST} {message}")
    } else {
        format!("{label} {message}")
    }
}

fn stdout_line(line: &str) {
    let mut stdout = std::io::stdout().lock();
    let _ = writeln!(stdout, "{line}");
    let _ = stdout.flush();
}

pub fn log(message: &str) {
    stdout_line(&format(
        BLU,
        "[zaribox]",
        message,
        std::io::stdout().is_terminal(),
    ));
}

pub fn ok(message: &str) {
    stdout_line(&format(
        GRN,
        "  ok",
        message,
        std::io::stdout().is_terminal(),
    ));
}

pub fn warn(message: &str) {
    stdout_line(&format(
        YLW,
        "  warn",
        message,
        std::io::stdout().is_terminal(),
    ));
}

pub fn err(message: &str) {
    let line = format(RED, "  error", message, std::io::stderr().is_terminal());
    let _ = std::io::stdout().flush();
    let _ = writeln!(std::io::stderr().lock(), "{line}");
}

/// `print()` equivalent that ignores a closed stdout.
pub fn print(text: &str) {
    stdout_line(text);
}
