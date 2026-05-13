use crate::interface::protocol::{RpcErrorDetail, RpcRequest, RpcResponse};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;

/// Handler function signature: takes params, returns Result<Value, RpcErrorDetail>
pub type HandlerFn =
    Arc<dyn Fn(Value) -> Result<Value, RpcErrorDetail> + Send + Sync>;

/// Route category: who handles this method
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RouteTarget {
    /// Handled locally in Rust (agent.*, system.*, cli.*)
    System,
    /// Forwarded to Python Backend via HTTP
    Backend,
}

/// Router: maps JSON-RPC method → handler or backend forward
pub struct Router {
    /// System handlers (agent.*, system.*, cli.*)
    system_handlers: HashMap<String, HandlerFn>,
    /// Methods forwarded to Python backend (knowledge.*, guide.*, search.*, stats.*)
    backend_methods: Vec<String>,
}

impl Router {
    pub fn new() -> Self {
        Self {
            system_handlers: HashMap::new(),
            backend_methods: Vec::new(),
        }
    }

    /// Register a system handler (runs in Rust)
    pub fn register_system<F>(&mut self, method: &str, handler: F)
    where
        F: Fn(Value) -> Result<Value, RpcErrorDetail> + Send + Sync + 'static,
    {
        self.system_handlers
            .insert(method.to_string(), Arc::new(handler));
    }

    /// Register a method to be forwarded to Python Backend
    pub fn register_backend(&mut self, method: &str) {
        self.backend_methods.push(method.to_string());
    }

    /// Determine the route target for a given method
    pub fn target(&self, method: &str) -> RouteTarget {
        if self.system_handlers.contains_key(method) {
            return RouteTarget::System;
        }
        if self.backend_methods.contains(&method.to_string()) {
            return RouteTarget::Backend;
        }
        // Default: route by prefix
        let prefix = method.split('.').next().unwrap_or("");
        match prefix {
            "agent" | "system" | "cli" => RouteTarget::System,
            _ => RouteTarget::Backend,
        }
    }

    /// Dispatch a system call locally
    pub fn dispatch_system(
        &self,
        request: &RpcRequest,
    ) -> Result<RpcResponse, (u64, RpcErrorDetail)> {
        match self.system_handlers.get(&request.method) {
            Some(handler) => match handler(request.params.clone()) {
                Ok(result) => Ok(RpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id: request.id,
                    result,
                }),
                Err(err) => Err((request.id, err)),
            },
            None => Err((
                request.id,
                RpcErrorDetail {
                    code: -32601,
                    message: format!("Method not found: {}", request.method),
                    data: None,
                },
            )),
        }
    }
}
