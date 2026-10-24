//! Windows authorization helpers. No credentials are sent to a WebView.
//! 上游自带的活跃统计上报（start_activity_reporter 等）在本 fork 已按策略移除，
//! 与 macOS 端保持一致：无遥测。githooks 黑名单会拦截回归。
use crate::{collector, panel};
use serde::Serialize;
use tauri::{AppHandle, WebviewWindow};

fn main_only(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == panel::LABEL {
        Ok(())
    } else {
        Err("Privacy controls belong to the main window".into())
    }
}

#[derive(Serialize)]
pub struct Authorization {
    status: String,
}

async fn authorization(
    app: AppHandle,
    window: WebviewWindow,
    mode: &'static str,
) -> Result<Authorization, String> {
    main_only(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let raw = collector::run_bridge(&app, mode)?;
        let value: serde_json::Value =
            serde_json::from_str(&raw).map_err(|_| "Invalid authorization result")?;
        let status = value
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("unavailable");
        let status = match status {
            "authorized"
            | "authorization_required"
            | "expired"
            | "login_required"
            | "disabled"
            | "unsupported_platform"
            | "unsupported_format" => status,
            _ => "unavailable",
        };
        Ok(Authorization {
            status: status.into(),
        })
    })
    .await
    .map_err(|_| "Authorization helper stopped".to_string())?
}

#[tauri::command]
pub async fn grok_bot_authorization_status(
    app: AppHandle,
    window: WebviewWindow,
) -> Result<Authorization, String> {
    authorization(app, window, "grok-status").await
}

#[tauri::command]
pub async fn authorize_grok_bot(
    app: AppHandle,
    window: WebviewWindow,
) -> Result<Authorization, String> {
    authorization(app, window, "grok-authorize").await
}
