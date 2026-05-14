use crate::interface::protocol::{RpcNotification, RpcOutgoing, RpcRequest};
use crate::interface::router::{RouteTarget, Router};
use futures_util::{SinkExt, StreamExt};
use reqwest::Client;
use serde_json::Value;
use std::sync::Arc;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::broadcast;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

/// The WS Gateway: manages connections and routes messages
pub struct WsGateway {
    router: Arc<Router>,
    http_client: Client,
    backend_url: String,
    /// Broadcast channel for push events to all connected frontends
    event_tx: broadcast::Sender<String>,
}

impl WsGateway {
    pub fn new(router: Router, backend_port: u16) -> Self {
        let (event_tx, _) = broadcast::channel(64);
        Self {
            router: Arc::new(router),
            http_client: Client::new(),
            backend_url: format!("http://127.0.0.1:{}/api/v1", backend_port),
            event_tx,
        }
    }

    /// Get a sender for pushing events
    pub fn event_sender(&self) -> broadcast::Sender<String> {
        self.event_tx.clone()
    }

    /// Push a notification to all connected frontends
    pub fn push_event(&self, method: &str, params: Value) {
        let notif = RpcOutgoing::Notification(RpcNotification {
            jsonrpc: "2.0".to_string(),
            method: method.to_string(),
            params,
        });
        let _ = self.event_tx.send(notif.to_json());
    }

    /// Start the WS server, listening on the given port
    pub async fn start(self: Arc<Self>, port: u16) -> Result<(), Box<dyn std::error::Error>> {
        let addr = format!("127.0.0.1:{}", port);
        let listener = TcpListener::bind(&addr).await?;
        println!("WS Gateway listening on ws://{}", addr);

        loop {
            let (stream, peer) = listener.accept().await?;
            println!("WS client connected: {}", peer);

            let gateway = self.clone();
            tokio::spawn(async move {
                if let Err(e) = gateway.handle_connection(stream).await {
                    eprintln!("WS connection error ({}): {}", peer, e);
                }
                println!("WS client disconnected: {}", peer);
            });
        }
    }

    /// Handle a single WebSocket connection
    async fn handle_connection(
        self: Arc<Self>,
        stream: TcpStream,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let ws_stream = accept_async(stream).await?;
        let (mut ws_sender, mut ws_receiver) = ws_stream.split();

        // Subscribe to events
        let mut event_rx = self.event_tx.subscribe();

        // Read loop: handle incoming messages
        loop {
            tokio::select! {
                // Incoming WS message from frontend
                msg = ws_receiver.next() => {
                    match msg {
                        Some(Ok(Message::Text(text))) => {
                            let response = self.handle_message(&text).await;
                            let _ = ws_sender.send(Message::Text(response.into())).await;
                        }
                        Some(Ok(Message::Close(_))) => break,
                        Some(Ok(Message::Ping(data))) => {
                            let _ = ws_sender.send(Message::Pong(data)).await;
                        }
                        Some(Err(e)) => {
                            eprintln!("WS read error: {}", e);
                            break;
                        }
                        None => break,
                        _ => {}
                    }
                }

                // Push event from system
                event_msg = event_rx.recv() => {
                    if let Ok(msg) = event_msg {
                        let _ = ws_sender.send(Message::Text(msg.into())).await;
                    }
                }
            }
        }

        Ok(())
    }

    /// Handle a single JSON-RPC message and return the response string
    async fn handle_message(&self, text: &str) -> String {
        let request: RpcRequest = match serde_json::from_str(text) {
            Ok(req) => req,
            Err(e) => {
                return RpcOutgoing::Error(crate::interface::protocol::RpcError {
                    jsonrpc: "2.0".to_string(),
                    id: 0,
                    error: crate::interface::protocol::RpcErrorDetail {
                        code: -32700,
                        message: format!("Parse error: {}", e),
                        data: None,
                    },
                })
                .to_json();
            }
        };

        let target = self.router.target(&request.method);

        match target {
            RouteTarget::System => {
                match self.router.dispatch_system(&request) {
                    Ok(response) => RpcOutgoing::Response(response).to_json(),
                    Err((id, err)) => RpcOutgoing::Error(crate::interface::protocol::RpcError {
                        jsonrpc: "2.0".to_string(),
                        id,
                        error: err,
                    })
                    .to_json(),
                }
            }
            RouteTarget::Backend => {
                self.forward_to_backend(&request).await
            }
        }
    }

    /// Forward a request to the Python Backend via HTTP
    async fn forward_to_backend(&self, request: &RpcRequest) -> String {
        // Map JSON-RPC method to REST endpoint
        let (http_method, path) = self.method_to_http(&request.method);

        let url = format!("{}{}", self.backend_url, path);

        let result = match http_method {
            "GET" => {
                self.http_client
                    .get(&url)
                    .query(&request.params)
                    .send()
                    .await
            }
            "POST" => {
                self.http_client
                    .post(&url)
                    .json(&request.params)
                    .send()
                    .await
            }
            "PUT" => {
                self.http_client
                    .put(&url)
                    .json(&request.params)
                    .send()
                    .await
            }
            "DELETE" => {
                self.http_client
                    .delete(&url)
                    .query(&request.params)
                    .send()
                    .await
            }
            _ => {
                return RpcOutgoing::Error(crate::interface::protocol::RpcError {
                    jsonrpc: "2.0".to_string(),
                    id: request.id,
                    error: crate::interface::protocol::RpcErrorDetail {
                        code: -32601,
                        message: format!("Unknown method: {}", request.method),
                        data: None,
                    },
                })
                .to_json();
            }
        };

        match result {
            Ok(resp) => {
                let status = resp.status();
                match resp.json::<Value>().await {
                    Ok(body) => {
                        if status.is_success() {
                            RpcOutgoing::Response(crate::interface::protocol::RpcResponse {
                                jsonrpc: "2.0".to_string(),
                                id: request.id,
                                result: body,
                            })
                            .to_json()
                        } else {
                            let detail = body
                                .get("detail")
                                .and_then(|d| d.as_str())
                                .unwrap_or("Backend error")
                                .to_string();
                            RpcOutgoing::Error(crate::interface::protocol::RpcError {
                                jsonrpc: "2.0".to_string(),
                                id: request.id,
                                error: crate::interface::protocol::RpcErrorDetail {
                                    code: -32000,
                                    message: detail,
                                    data: Some(body),
                                },
                            })
                            .to_json()
                        }
                    }
                    Err(e) => RpcOutgoing::Error(crate::interface::protocol::RpcError {
                        jsonrpc: "2.0".to_string(),
                        id: request.id,
                        error: crate::interface::protocol::RpcErrorDetail {
                            code: -32603,
                            message: format!("Failed to parse backend response: {}", e),
                            data: None,
                        },
                    })
                    .to_json(),
                }
            }
            Err(e) => RpcOutgoing::Error(crate::interface::protocol::RpcError {
                jsonrpc: "2.0".to_string(),
                id: request.id,
                error: crate::interface::protocol::RpcErrorDetail {
                    code: -32000,
                    message: if e.is_connect() {
                        "Backend not available".to_string()
                    } else {
                        format!("Backend error: {}", e)
                    },
                    data: None,
                },
            })
            .to_json(),
        }
    }

    /// Map JSON-RPC method to (HTTP method, REST path)
    fn method_to_http(&self, method: &str) -> (&str, String) {
        match method {
            // Guide
            "guide.ask" => ("POST", "/guide/ask".to_string()),
            "guide.status" => ("GET", "/guide/status".to_string()),
            "guide.quick_questions" => ("GET", "/guide/quick_questions".to_string()),

            // Knowledge CRUD
            "knowledge.create" => ("POST", "/knowledge".to_string()),
            "knowledge.search" => ("POST", "/knowledge/search".to_string()),
            "knowledge.get" => ("GET", {
                // path will be set dynamically based on params in forward_to_backend
                "/knowledge/by_id".to_string()
            }),
            "knowledge.update" => ("PUT", {
                "/knowledge/by_id".to_string()
            }),
            "knowledge.delete" => ("DELETE", {
                "/knowledge/by_id".to_string()
            }),
            "knowledge.feedback" => ("POST", "/knowledge/feedback".to_string()),
            "knowledge.related" => ("GET", "/knowledge/related".to_string()),
            "knowledge.tags" => ("GET", "/knowledge/tags".to_string()),
            "knowledge.by_tag" => ("GET", "/knowledge/by_tag".to_string()),

            // Stats
            "stats.overview" => ("GET", "/stats".to_string()),
            "stats.timeline" => ("GET", "/timeline".to_string()),
            "stats.events" => ("GET", "/events/recent".to_string()),

            // Default
            _ => {
                let path = format!("/{}", method.replace('.', "/"));
                ("POST", path)
            }
        }
    }
}
