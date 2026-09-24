//! MCP (Model Context Protocol) stdio server exposing AgentBox tools.
//!
//! Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout and implements the
//! subset MCP clients use: `initialize`, `ping`, `tools/list`, `tools/call`.
//! Tools only operate on versioned AgentBox manifests inside the project root
//! (`$ZARIBOX_MCP_ROOT`, default: the working directory).

use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use anyhow::{Context, Result, bail, ensure};
use serde::Deserialize;
use serde::de::DeserializeOwned;
use serde_json::{Value, json};

use crate::config::{Manifest, Profile, StringMap};
use crate::paths;
use crate::service::{DEFAULT_LOCK_TIMEOUT, EnsureOptions, ExecRequest, Service};

const OUTPUT_LIMIT: usize = 1024 * 1024;
const DEFAULT_MAX_TIMEOUT: Duration = Duration::from_secs(900);
const DEFAULT_EXEC_TIMEOUT: Duration = Duration::from_secs(300);
const PROTOCOL_VERSIONS: &[&str] = &["2025-06-18", "2025-03-26", "2024-11-05"];

const INSTRUCTIONS: &str = "Use zaribox_validate to check a manifest and zaribox_plan to preview \
changes, then zaribox_create to create or update the AgentBox. Use zaribox_status to inspect \
state and zaribox_exec for bounded, non-interactive work. Destructive reconciliation requires \
allow_destructive=true. Removal requires confirm=true and preserves the dedicated home \
directory. Tools only accept versioned AgentBox manifests inside ZARIBOX_MCP_ROOT; desktop \
boxes, host-home access, interactive shells, and root command execution are intentionally \
unavailable. A target is either an AgentBox name or a manifest path inside the project root.";

// ---------------------------------------------------------------------------
// Tool arguments

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ManifestArgs {
    manifest: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CreateArgs {
    manifest: String,
    #[serde(default)]
    allow_destructive: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TargetArgs {
    target: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ExecArgs {
    target: String,
    argv: Vec<String>,
    /// Seconds; defaults to 300 (capped at the server maximum).
    timeout: Option<f64>,
    workdir: Option<String>,
    #[serde(default)]
    env: StringMap,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RemoveArgs {
    target: String,
    #[serde(default)]
    confirm: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NoArgs {}

struct ToolSpec {
    name: &'static str,
    title: &'static str,
    description: &'static str,
    /// (read_only, destructive, idempotent, open_world)
    hints: (bool, bool, bool, bool),
    schema: fn() -> Value,
}

fn string_prop(description: &str) -> Value {
    json!({ "type": "string", "description": description })
}

const MANIFEST_DESC: &str = "YAML manifest path relative to the project root";
const TARGET_DESC: &str = "AgentBox name or manifest path inside the project root";

const TOOLS: &[ToolSpec] = &[
    ToolSpec {
        name: "zaribox_validate",
        title: "Validate AgentBox Manifest",
        description: "Validate an AgentBox manifest without creating or changing anything. \
            Checks its schema, image, mounts, resource limits, and security policy, and returns \
            the resolved manifest path, container name, image, and security profile.",
        hints: (true, false, true, false),
        schema: || {
            json!({ "type": "object", "properties": { "manifest": string_prop(MANIFEST_DESC) },
                    "required": ["manifest"], "additionalProperties": false })
        },
    },
    ToolSpec {
        name: "zaribox_plan",
        title: "Plan AgentBox Changes",
        description: "Preview every action needed to create or update an AgentBox. Makes no \
            changes. `requires_force` tells whether zaribox_create needs allow_destructive=true.",
        hints: (true, false, true, false),
        schema: || {
            json!({ "type": "object", "properties": { "manifest": string_prop(MANIFEST_DESC) },
                    "required": ["manifest"], "additionalProperties": false })
        },
    },
    ToolSpec {
        name: "zaribox_create",
        title: "Create or Update AgentBox",
        description: "Create a missing AgentBox or reconcile an existing one with its manifest; \
            the dedicated home directory persists. Keep allow_destructive false unless a prior \
            plan reports requires_force and the change is intentional.",
        hints: (false, true, true, true),
        schema: || {
            json!({ "type": "object", "properties": {
                        "manifest": string_prop(MANIFEST_DESC),
                        "allow_destructive": { "type": "boolean", "default": false } },
                    "required": ["manifest"], "additionalProperties": false })
        },
    },
    ToolSpec {
        name: "zaribox_status",
        title: "Inspect AgentBox Status",
        description: "Report configuration and runtime state for a managed AgentBox: existence, \
            image, security profile, expiry, configuration sync, and package drift.",
        hints: (true, false, true, false),
        schema: || {
            json!({ "type": "object", "properties": { "target": string_prop(TARGET_DESC) },
                    "required": ["target"], "additionalProperties": false })
        },
    },
    ToolSpec {
        name: "zaribox_exec",
        title: "Execute Command in AgentBox",
        description: "Run a bounded, non-interactive command as the AgentBox user. Pass the \
            executable and each argument separately in argv; shell syntax is not interpreted. \
            Output is capped at 1 MiB. Root execution and interactive sessions are unavailable.",
        hints: (false, true, false, true),
        schema: || {
            json!({ "type": "object", "properties": {
                        "target": string_prop(TARGET_DESC),
                        "argv": { "type": "array", "items": { "type": "string" }, "minItems": 1 },
                        "timeout": { "type": "number", "default": 300,
                                     "description": "Seconds; bounded by ZARIBOX_MCP_MAX_TIMEOUT" },
                        "workdir": string_prop("Working directory inside the container"),
                        "env": { "type": "object", "additionalProperties": { "type": "string" } } },
                    "required": ["target", "argv"], "additionalProperties": false })
        },
    },
    ToolSpec {
        name: "zaribox_remove",
        title: "Remove AgentBox",
        description: "Remove a managed AgentBox while preserving its home directory. Requires \
            confirm=true.",
        hints: (false, true, true, false),
        schema: || {
            json!({ "type": "object", "properties": {
                        "target": string_prop(TARGET_DESC),
                        "confirm": { "type": "boolean", "default": false } },
                    "required": ["target"], "additionalProperties": false })
        },
    },
    ToolSpec {
        name: "zaribox_list",
        title: "List Project AgentBoxes",
        description: "List managed agent-profile boxes whose manifests are inside the project \
            root, with runtime state, image, digest, and expiry.",
        hints: (true, false, true, false),
        schema: || json!({ "type": "object", "properties": {}, "additionalProperties": false }),
    },
];

fn tool_descriptors() -> Vec<Value> {
    TOOLS
        .iter()
        .map(|tool| {
            let (read_only, destructive, idempotent, open_world) = tool.hints;
            json!({
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": (tool.schema)(),
                "annotations": {
                    "readOnlyHint": read_only,
                    "destructiveHint": destructive,
                    "idempotentHint": idempotent,
                    "openWorldHint": open_world,
                },
            })
        })
        .collect()
}

fn parse_args<T: DeserializeOwned>(args: Value) -> Result<T> {
    let args = if args.is_null() { json!({}) } else { args };
    serde_json::from_value(args).context("invalid tool arguments")
}

// ---------------------------------------------------------------------------
// Project-scoped facade

pub struct Tools {
    service: Service,
    root: PathBuf,
    max_timeout: Duration,
}

impl Tools {
    pub fn new(
        service: Service,
        root: Option<PathBuf>,
        max_timeout: Option<Duration>,
    ) -> Result<Self> {
        let root = match root {
            Some(root) => root,
            None => match paths::env_nonempty("ZARIBOX_MCP_ROOT") {
                Some(dir) => paths::expand(&dir),
                None => {
                    std::env::current_dir().context("cannot determine the current directory")?
                }
            },
        };
        let root = paths::canonical(&root);
        ensure!(
            root.is_dir(),
            "MCP project root does not exist: {}",
            root.display()
        );
        let max_timeout = match max_timeout {
            Some(timeout) => timeout,
            None => match paths::env_nonempty("ZARIBOX_MCP_MAX_TIMEOUT") {
                Some(raw) => raw
                    .trim()
                    .parse::<f64>()
                    .ok()
                    .filter(|secs| *secs > 0.0)
                    .and_then(|secs| Duration::try_from_secs_f64(secs).ok())
                    .context("ZARIBOX_MCP_MAX_TIMEOUT must be a positive number of seconds")?,
                None => DEFAULT_MAX_TIMEOUT,
            },
        };
        ensure!(
            !max_timeout.is_zero(),
            "the maximum timeout must be positive"
        );
        Ok(Self {
            service,
            root,
            max_timeout,
        })
    }

    fn inside_root(&self, path: &Path) -> Result<PathBuf> {
        let resolved = paths::canonical(self.root.join(path));
        ensure!(
            resolved.starts_with(&self.root),
            "path is outside the MCP project root '{}': {}",
            self.root.display(),
            resolved.display()
        );
        Ok(resolved)
    }

    fn load(&self, path: &Path) -> Result<Manifest> {
        let text = path.to_str().context("manifest path is not valid UTF-8")?;
        self.service.load(Some(text))
    }

    /// Resolve a manifest path (`manifest = true`) or an AgentBox name/path.
    fn agent_manifest(&self, target: &str, manifest: bool) -> Result<Manifest> {
        let direct = Path::new(target);
        let looks_like_path = manifest
            || direct.is_absolute()
            || target.contains('/')
            || matches!(
                direct.extension().and_then(|e| e.to_str()),
                Some("yaml" | "yml")
            )
            || self.root.join(direct).is_file();
        let config = if looks_like_path {
            self.load(&self.inside_root(direct)?)?
        } else {
            let config = self.service.load(Some(target))?;
            self.inside_root(&config.path)?;
            config
        };
        ensure!(
            config.is_agent_box(),
            "MCP tools only operate on versioned AgentBox manifests"
        );
        Ok(config)
    }

    fn manifest_arg(&self, manifest: &str) -> Result<String> {
        let config = self.agent_manifest(manifest, true)?;
        Ok(config.path.to_string_lossy().into_owned())
    }

    /// Dispatch a tool call; the result is always a JSON object.
    pub fn call(&self, name: &str, args: Value) -> Result<Value> {
        let value = match name {
            "zaribox_validate" => {
                let args: ManifestArgs = parse_args(args)?;
                let path = self.manifest_arg(&args.manifest)?;
                serde_json::to_value(self.service.validate(Some(&path))?)?
            }
            "zaribox_plan" => {
                let args: ManifestArgs = parse_args(args)?;
                let path = self.manifest_arg(&args.manifest)?;
                serde_json::to_value(self.service.plan(Some(&path))?)?
            }
            "zaribox_create" => {
                let args: CreateArgs = parse_args(args)?;
                let path = self.manifest_arg(&args.manifest)?;
                let options = EnsureOptions {
                    force: args.allow_destructive,
                    ..EnsureOptions::default()
                };
                serde_json::to_value(self.service.ensure(Some(&path), options)?)?
            }
            "zaribox_status" => {
                let args: TargetArgs = parse_args(args)?;
                let config = self.agent_manifest(&args.target, false)?;
                serde_json::to_value(self.service.status(&config.path.to_string_lossy())?)?
            }
            "zaribox_exec" => {
                let args: ExecArgs = parse_args(args)?;
                ensure!(
                    !args.argv.is_empty(),
                    "argv must contain at least one argument"
                );
                let timeout = match args.timeout {
                    Some(secs) => Duration::try_from_secs_f64(secs).ok(),
                    None => Some(DEFAULT_EXEC_TIMEOUT.min(self.max_timeout)),
                };
                let timeout = timeout
                    .filter(|t| !t.is_zero() && *t <= self.max_timeout)
                    .with_context(|| {
                        format!(
                            "timeout must be between 0 and {} seconds",
                            self.max_timeout.as_secs_f64()
                        )
                    })?;
                let config = self.agent_manifest(&args.target, false)?;
                let request = ExecRequest {
                    argv: args.argv,
                    timeout,
                    max_output: OUTPUT_LIMIT,
                    as_root: false,
                    workdir: args.workdir,
                    env: args.env,
                };
                serde_json::to_value(self.service.exec(&config.path.to_string_lossy(), request)?)?
            }
            "zaribox_remove" => {
                let args: RemoveArgs = parse_args(args)?;
                ensure!(args.confirm, "remove requires confirm=true");
                let config = self.agent_manifest(&args.target, false)?;
                let path = config.path.to_string_lossy();
                serde_json::to_value(self.service.destroy(&path, DEFAULT_LOCK_TIMEOUT)?)?
            }
            "zaribox_list" => {
                let _: NoArgs = parse_args(args)?;
                let boxes: Vec<_> = self
                    .service
                    .list()?
                    .into_iter()
                    .filter(|b| b.security_profile == Profile::Agent)
                    .filter(|b| self.inside_root(&b.config_path).is_ok())
                    .collect();
                // Structured tool results must be objects.
                json!({ "result": boxes })
            }
            other => bail!("unknown tool: {other}"),
        };
        Ok(value)
    }
}

// ---------------------------------------------------------------------------
// JSON-RPC transport

#[derive(Deserialize)]
struct Request {
    #[serde(default)]
    id: Option<Value>,
    method: String,
    #[serde(default)]
    params: Value,
}

#[derive(Deserialize)]
struct CallParams {
    name: String,
    #[serde(default)]
    arguments: Value,
}

fn rpc_error(id: Value, code: i64, message: impl Into<String>) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message.into() } })
}

fn rpc_result(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

impl Tools {
    fn tool_result(&self, params: Value) -> std::result::Result<Value, (i64, String)> {
        let params: CallParams =
            serde_json::from_value(params).map_err(|e| (-32602, format!("invalid params: {e}")))?;
        if !TOOLS.iter().any(|t| t.name == params.name) {
            return Err((-32602, format!("unknown tool: {}", params.name)));
        }
        Ok(match self.call(&params.name, params.arguments) {
            Ok(value) => json!({
                "content": [{ "type": "text", "text": value.to_string() }],
                "structuredContent": value,
                "isError": false,
            }),
            Err(error) => json!({
                "content": [{ "type": "text", "text": format!("{error:#}") }],
                "isError": true,
            }),
        })
    }

    /// Handle one JSON-RPC message; notifications produce no response.
    pub fn handle(&self, line: &str) -> Option<Value> {
        let request: Request = match serde_json::from_str(line) {
            Ok(request) => request,
            Err(error) => {
                return Some(rpc_error(
                    Value::Null,
                    -32700,
                    format!("parse error: {error}"),
                ));
            }
        };
        let id = request.id?;
        let response = match request.method.as_str() {
            "initialize" => {
                let requested = request.params["protocolVersion"]
                    .as_str()
                    .unwrap_or_default();
                let version = PROTOCOL_VERSIONS
                    .iter()
                    .find(|v| **v == requested)
                    .unwrap_or(&PROTOCOL_VERSIONS[0]);
                rpc_result(
                    id,
                    json!({
                        "protocolVersion": version,
                        "capabilities": { "tools": { "listChanged": false } },
                        "serverInfo": {
                            "name": "zaribox",
                            "title": "ZariBox",
                            "version": env!("CARGO_PKG_VERSION"),
                        },
                        "instructions": INSTRUCTIONS,
                    }),
                )
            }
            "ping" => rpc_result(id, json!({})),
            "tools/list" => rpc_result(id, json!({ "tools": tool_descriptors() })),
            "tools/call" => match self.tool_result(request.params) {
                Ok(result) => rpc_result(id, result),
                Err((code, message)) => rpc_error(id, code, message),
            },
            other => rpc_error(id, -32601, format!("method not found: {other}")),
        };
        Some(response)
    }

    /// Serve requests from stdin until EOF.
    pub fn serve(&self) -> Result<()> {
        let stdin = std::io::stdin();
        let mut stdout = std::io::stdout().lock();
        for line in stdin.lock().lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            if let Some(response) = self.handle(&line) {
                writeln!(stdout, "{response}")?;
                stdout.flush()?;
            }
        }
        Ok(())
    }
}

pub fn main() -> ExitCode {
    let result = Tools::new(Service::default(), None, None).and_then(|tools| tools.serve());
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("zaribox-mcp: {error:#}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests;
