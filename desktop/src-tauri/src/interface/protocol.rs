use serde::{Deserialize, Serialize};
use serde_json::Value;

/// JSON-RPC 2.0 request (from frontend)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RpcRequest {
    pub jsonrpc: String,
    pub id: u64,
    pub method: String,
    #[serde(default)]
    pub params: Value,
}

/// JSON-RPC 2.0 success response
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RpcResponse {
    pub jsonrpc: String,
    pub id: u64,
    pub result: Value,
}

/// JSON-RPC 2.0 error response
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RpcError {
    pub jsonrpc: String,
    pub id: u64,
    pub error: RpcErrorDetail,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RpcErrorDetail {
    pub code: i64,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

/// JSON-RPC 2.0 notification / push event (no id)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RpcNotification {
    pub jsonrpc: String,
    pub method: String,
    #[serde(default)]
    pub params: Value,
}

/// Anything that can be sent over WS to the frontend
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum RpcOutgoing {
    Response(RpcResponse),
    Error(RpcError),
    Notification(RpcNotification),
}

impl RpcOutgoing {
    pub fn to_json(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| {
            r#"{"jsonrpc":"2.0","id":0,"error":{"code":-32603,"message":"serialization failed"}}"#
                .to_string()
        })
    }
}
