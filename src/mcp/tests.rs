use super::*;
use crate::service::tests::{Sandbox, service};

const AGENT: &str = "ApiVersion: zaribox.dev/v1\nKind: AgentBox\nRuntime:\n  Image: alpine\n";

fn tools(sandbox: &Sandbox) -> Tools {
    let (service, _) = service();
    Tools::new(
        service,
        Some(sandbox.dir.path().to_path_buf()),
        Some(Duration::from_secs(60)),
    )
    .unwrap()
}

fn call(tools: &Tools, name: &str, args: Value) -> Value {
    let line = json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": { "name": name, "arguments": args } });
    tools.handle(&line.to_string()).unwrap()
}

fn error_text(response: &Value) -> String {
    assert_eq!(response["result"]["isError"], true, "{response}");
    response["result"]["content"][0]["text"]
        .as_str()
        .unwrap()
        .to_string()
}

#[test]
fn protocol_basics() {
    let sandbox = Sandbox::new();
    let tools = tools(&sandbox);
    let init = tools
        .handle(r#"{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}"#)
        .unwrap();
    assert_eq!(init["result"]["protocolVersion"], "2025-03-26");
    assert!(
        tools
            .handle(r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#)
            .is_none()
    );
    let list = tools
        .handle(r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#)
        .unwrap();
    assert_eq!(list["result"]["tools"].as_array().unwrap().len(), 7);
    assert_eq!(
        tools.handle(r#"{"id":3,"method":"nope"}"#).unwrap()["error"]["code"],
        -32601
    );
    assert_eq!(tools.handle("{").unwrap()["error"]["code"], -32700);
    assert_eq!(
        call(&tools, "zaribox_shell", json!({}))["error"]["code"],
        -32602
    );
}

#[test]
fn agent_lifecycle() {
    let sandbox = Sandbox::new();
    sandbox.manifest("agent.yaml", AGENT);
    let tools = tools(&sandbox);
    let validated = call(
        &tools,
        "zaribox_validate",
        json!({ "manifest": "agent.yaml" }),
    );
    assert_eq!(
        validated["result"]["structuredContent"]["security_profile"],
        "agent"
    );
    let created = call(
        &tools,
        "zaribox_create",
        json!({ "manifest": "agent.yaml" }),
    );
    assert_eq!(
        created["result"]["structuredContent"]["actions"][0],
        "create"
    );
    let exec = call(
        &tools,
        "zaribox_exec",
        json!({ "target": "agent", "argv": ["id"] }),
    );
    assert_eq!(exec["result"]["isError"], false, "{exec}");
    assert_eq!(exec["result"]["structuredContent"]["exit_code"], 0);
    let list = call(&tools, "zaribox_list", json!({}));
    assert_eq!(
        list["result"]["structuredContent"]["result"][0]["name"],
        "agent"
    );
    let refused = call(&tools, "zaribox_remove", json!({ "target": "agent" }));
    assert!(error_text(&refused).contains("confirm=true"));
    let removed = call(
        &tools,
        "zaribox_remove",
        json!({ "target": "agent", "confirm": true }),
    );
    assert_eq!(removed["result"]["structuredContent"]["changed"], true);
}

#[test]
fn rejects_unsafe_requests() {
    let sandbox = Sandbox::new();
    sandbox.manifest("desk.yaml", "Image: archlinux\n");
    sandbox.manifest("agent.yaml", AGENT);
    let tools = tools(&sandbox);
    for (name, args, needle) in [
        (
            "zaribox_validate",
            json!({ "manifest": "desk.yaml" }),
            "versioned AgentBox",
        ),
        (
            "zaribox_validate",
            json!({ "manifest": "../x.yaml" }),
            "outside the MCP project root",
        ),
        (
            "zaribox_validate",
            json!({ "manifest": "/etc/passwd" }),
            "outside the MCP project root",
        ),
        (
            "zaribox_validate",
            json!({ "manifest": "agent.yaml", "extra": 1 }),
            "invalid tool arguments",
        ),
        (
            "zaribox_exec",
            json!({ "target": "agent", "argv": [] }),
            "at least one",
        ),
        (
            "zaribox_exec",
            json!({ "target": "agent", "argv": ["id"], "timeout": 61 }),
            "timeout must be",
        ),
        (
            "zaribox_exec",
            json!({ "target": "agent", "argv": ["id"], "timeout": -1 }),
            "timeout must be",
        ),
    ] {
        let text = error_text(&call(&tools, name, args.clone()));
        assert!(text.contains(needle), "{name} {args}: {text}");
    }
}
