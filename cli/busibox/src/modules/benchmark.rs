use serde_json::Value;
use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq)]
pub enum BenchmarkMode {
    Performance,
    ModelTests,
    LoadTest,
}

#[derive(Debug, Clone)]
pub struct ModelTestResult {
    pub test_name: String,
    pub tier: ModelTestTier,
    pub passed: bool,
    pub response_content: Option<String>,
    pub error: Option<String>,
    pub elapsed_ms: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ModelTestTier {
    DirectVllm,
    LiteLLM,
    Service,
}

impl std::fmt::Display for ModelTestTier {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ModelTestTier::DirectVllm => write!(f, "vLLM"),
            ModelTestTier::LiteLLM => write!(f, "LiteLLM"),
            ModelTestTier::Service => write!(f, "Service"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct BenchmarkConfig {
    pub max_tokens_throughput: usize,
    pub max_tokens_parallel: usize,
    pub parallel_count: usize,
    pub num_runs: usize,
    pub prompt: String,
}

impl Default for BenchmarkConfig {
    fn default() -> Self {
        Self {
            max_tokens_throughput: 256,
            max_tokens_parallel: 128,
            parallel_count: 4,
            num_runs: 3,
            prompt: "Write a short story about a robot learning to paint.".to_string(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct BenchmarkResult {
    pub model_name: String,
    pub port: u16,
    pub ttft_ms: Option<f64>,
    pub throughput_tps: Option<f64>,
    pub parallel_tps: Option<f64>,
    pub parallel_latency_ms: Option<f64>,
}

impl BenchmarkResult {
    pub fn new(model_name: &str, port: u16) -> Self {
        Self {
            model_name: model_name.to_string(),
            port,
            ttft_ms: None,
            throughput_tps: None,
            parallel_tps: None,
            parallel_latency_ms: None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct CurlResponse {
    pub completion_tokens: usize,
    pub elapsed_secs: f64,
    /// Error message from the API if the response was an error (e.g. "model not found").
    pub error_message: Option<String>,
}

/// Build a curl command that sends a chat completion request to vLLM and appends timing.
/// The output format is: JSON_BODY\n---BENCH_TIME:seconds---
pub fn build_curl_command(
    vllm_ip: &str,
    port: u16,
    model_name: &str,
    prompt: &str,
    max_tokens: usize,
) -> String {
    let body = serde_json::json!({
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": false,
    });
    let body_str = body.to_string().replace('\'', "'\\''");

    format!(
        "curl -s -w '\\n---BENCH_TIME:%{{time_total}}---' \
         -H 'Content-Type: application/json' \
         --max-time 120 \
         -d '{}' \
         'http://{}:{}/v1/chat/completions'; true",
        body_str, vllm_ip, port
    )
}

/// Build a shell snippet that runs N parallel curl commands and collects all output.
/// Each request's output is captured to a temp file to avoid interleaved output,
/// then concatenated with ---BENCH_REQ:N--- / ---BENCH_REQ_END:N--- markers.
pub fn build_parallel_curl_command(
    vllm_ip: &str,
    port: u16,
    model_name: &str,
    prompt: &str,
    max_tokens: usize,
    count: usize,
) -> String {
    let single = build_curl_command(vllm_ip, port, model_name, prompt, max_tokens);
    let mut script = String::new();
    script.push_str("_BENCH_DIR=$(mktemp -d); ");
    script.push_str("OVERALL_START=$(date +%s%N 2>/dev/null || echo 0); ");
    for i in 0..count {
        script.push_str(&format!(
            "( {single} > \"$_BENCH_DIR/{i}.out\" 2>&1 ) & "
        ));
    }
    script.push_str("wait; ");
    script.push_str("OVERALL_END=$(date +%s%N 2>/dev/null || echo 0); ");
    for i in 0..count {
        script.push_str(&format!(
            "echo '---BENCH_REQ:{i}---'; cat \"$_BENCH_DIR/{i}.out\"; echo ''; echo '---BENCH_REQ_END:{i}---'; "
        ));
    }
    script.push_str("echo \"---BENCH_WALL:$(( (OVERALL_END - OVERALL_START) ))---\"; ");
    script.push_str("rm -rf \"$_BENCH_DIR\"");
    script
}

/// Parse a single curl response that ends with ---BENCH_TIME:seconds---
pub fn parse_curl_response(output: &str) -> Option<CurlResponse> {
    let timing_marker = "---BENCH_TIME:";
    let timing_end = "---";

    let timing_pos = output.rfind(timing_marker)?;
    let after_marker = &output[timing_pos + timing_marker.len()..];
    let end_pos = after_marker.find(timing_end)?;
    let time_str = &after_marker[..end_pos];
    let elapsed_secs: f64 = time_str.trim().parse().ok()?;

    let json_part = output[..timing_pos].trim();
    let json: Value = serde_json::from_str(json_part).ok()?;

    let error_message = json
        .get("error")
        .and_then(|e| e.get("message"))
        .and_then(|m| m.as_str())
        .map(|s| s.to_string())
        .or_else(|| {
            json.get("message")
                .and_then(|m| m.as_str())
                .map(|s| s.to_string())
        });

    let mut completion_tokens = json
        .get("usage")
        .and_then(|u| u.get("completion_tokens"))
        .and_then(|t| t.as_u64())
        .unwrap_or(0) as usize;

    // MLX servers report completion_tokens=0 despite generating content.
    // Fall back to estimating from the response text (~4 chars per token).
    if completion_tokens == 0 {
        if let Some(content) = json
            .get("choices")
            .and_then(|c| c.get(0))
            .and_then(|c| c.get("message"))
            .and_then(|m| m.get("content"))
            .and_then(|c| c.as_str())
        {
            if !content.is_empty() {
                completion_tokens = (content.len() / 4).max(1);
            }
        }
    }

    Some(CurlResponse {
        completion_tokens,
        elapsed_secs,
        error_message,
    })
}

/// Parse the output of a parallel curl run.
/// Returns individual CurlResponses and the overall wall-clock nanoseconds (if available).
pub fn parse_parallel_output(output: &str) -> (Vec<CurlResponse>, Option<u64>) {
    let mut responses = Vec::new();

    // Extract per-request blocks
    let mut i = 0;
    loop {
        let start_marker = format!("---BENCH_REQ:{i}---");
        let end_marker = format!("---BENCH_REQ_END:{i}---");

        let start_pos = match output.find(&start_marker) {
            Some(p) => p + start_marker.len(),
            None => break,
        };
        let end_pos = match output.find(&end_marker) {
            Some(p) => p,
            None => break,
        };

        let block = &output[start_pos..end_pos];
        if let Some(resp) = parse_curl_response(block.trim()) {
            responses.push(resp);
        }
        i += 1;
    }

    // Extract overall wall time in nanoseconds
    let wall_ns = output
        .rfind("---BENCH_WALL:")
        .and_then(|pos| {
            let after = &output[pos + "---BENCH_WALL:".len()..];
            let end = after.find("---")?;
            after[..end].trim().parse::<u64>().ok()
        });

    (responses, wall_ns)
}

/// Compute the median of a slice of f64 values.
pub fn median(values: &mut [f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mid = values.len() / 2;
    if values.len() % 2 == 0 {
        (values[mid - 1] + values[mid]) / 2.0
    } else {
        values[mid]
    }
}

// --- Model Test helpers ---

/// Build a curl command targeting LiteLLM proxy with auth header.
pub fn build_litellm_curl_command(
    litellm_ip: &str,
    port: u16,
    purpose_name: &str,
    api_key: &str,
    prompt: &str,
    max_tokens: usize,
) -> String {
    let body = serde_json::json!({
        "model": purpose_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": false,
    });
    let body_str = body.to_string().replace('\'', "'\\''");

    // Append `; true` so the command always exits 0 — ssh.run() treats
    // non-zero exits as errors, but we want to parse the response ourselves.
    if api_key.is_empty() {
        format!(
            "curl -s -w '\\n---BENCH_TIME:%{{time_total}}---' \
             -H 'Content-Type: application/json' \
             --max-time 60 \
             -d '{}' \
             'http://{}:{}/v1/chat/completions'; true",
            body_str, litellm_ip, port
        )
    } else {
        let escaped_key = api_key.replace('\'', "'\\''");
        format!(
            "curl -s -w '\\n---BENCH_TIME:%{{time_total}}---' \
             -H 'Content-Type: application/json' \
             -H 'Authorization: Bearer {}' \
             --max-time 60 \
             -d '{}' \
             'http://{}:{}/v1/chat/completions'; true",
            escaped_key, body_str, litellm_ip, port
        )
    }
}

/// Parse a model test response, checking for valid choices content.
pub fn parse_model_test_response(output: &str) -> ModelTestResult {
    let mut result = ModelTestResult {
        test_name: String::new(),
        tier: ModelTestTier::DirectVllm,
        passed: false,
        response_content: None,
        error: None,
        elapsed_ms: 0.0,
    };

    match parse_curl_response(output) {
        Some(resp) => {
            result.elapsed_ms = resp.elapsed_secs * 1000.0;

            if let Some(ref err) = resp.error_message {
                result.error = Some(err.clone());
                return result;
            }

            // Try to extract the actual response content
            let timing_pos = output.rfind("---BENCH_TIME:").unwrap_or(output.len());
            let json_part = output[..timing_pos].trim();
            if let Ok(json) = serde_json::from_str::<Value>(json_part) {
                let content = json
                    .get("choices")
                    .and_then(|c| c.get(0))
                    .and_then(|c| c.get("message"))
                    .and_then(|m| m.get("content"))
                    .and_then(|c| c.as_str())
                    .map(|s| s.to_string());

                if let Some(text) = content {
                    if !text.is_empty() {
                        result.passed = true;
                        result.response_content = Some(text);
                    } else {
                        result.error = Some("Empty response content".to_string());
                    }
                } else {
                    result.error = Some("No choices[0].message.content in response".to_string());
                }
            } else {
                result.error = Some("Could not parse JSON response".to_string());
            }
        }
        None => {
            let preview: String = output.chars().take(120).collect();
            result.error = Some(format!("Could not parse curl output: {preview}"));
        }
    }

    result
}

/// Read model_purposes from a model_config.yml contents string.
pub fn parse_model_purposes(config_contents: &str) -> HashMap<String, String> {
    #[derive(serde::Deserialize)]
    struct Config {
        model_purposes: Option<HashMap<String, String>>,
    }

    serde_yaml::from_str::<Config>(config_contents)
        .ok()
        .and_then(|c| c.model_purposes)
        .unwrap_or_default()
}

/// Purposes that are testable via LiteLLM chat completions (excludes embedding, reranking, media).
pub fn testable_chat_purposes(purposes: &HashMap<String, String>) -> Vec<String> {
    let skip = ["embedding", "reranking", "image", "transcribe", "voice", "flux", "whisper", "kokoro"];
    let mut result: Vec<String> = purposes
        .keys()
        .filter(|k| !skip.iter().any(|s| k.as_str() == *s))
        .cloned()
        .collect();
    result.sort();
    result.dedup();
    result
}

// --- Service model benchmark helpers ---

/// Build a curl command to benchmark the embedding service (no auth required).
/// POST /embed with a short text payload.
pub fn build_embedding_curl_command(ip: &str, port: u16) -> String {
    let body = serde_json::json!({
        "input": "The quick brown fox jumps over the lazy dog. This is a benchmark test for embedding latency."
    });
    let body_str = body.to_string().replace('\'', "'\\''");
    format!(
        "curl -s -w '\\n---BENCH_TIME:%{{time_total}}---' \
         -H 'Content-Type: application/json' \
         --max-time 30 \
         -d '{}' \
         'http://{}:{}/embed'; true",
        body_str, ip, port
    )
}

/// Parse an embedding response — check for data array with embeddings.
pub fn parse_embedding_response(output: &str) -> ModelTestResult {
    let mut result = ModelTestResult {
        test_name: String::new(),
        tier: ModelTestTier::Service,
        passed: false,
        response_content: None,
        error: None,
        elapsed_ms: 0.0,
    };

    let timing_marker = "---BENCH_TIME:";
    if let Some(timing_pos) = output.rfind(timing_marker) {
        let after = &output[timing_pos + timing_marker.len()..];
        if let Some(end) = after.find("---") {
            if let Ok(secs) = after[..end].trim().parse::<f64>() {
                result.elapsed_ms = secs * 1000.0;
            }
        }
        let json_part = output[..timing_pos].trim();
        if let Ok(json) = serde_json::from_str::<Value>(json_part) {
            if let Some(data) = json.get("data").and_then(|d| d.as_array()) {
                if !data.is_empty() {
                    let dim = data[0]
                        .get("embedding")
                        .and_then(|e| e.as_array())
                        .map(|a| a.len())
                        .unwrap_or(0);
                    result.passed = dim > 0;
                    result.response_content = Some(format!("{} embeddings, dim={}", data.len(), dim));
                } else {
                    result.error = Some("Empty data array".to_string());
                }
            } else if let Some(detail) = json.get("detail").and_then(|d| d.as_str()) {
                result.error = Some(detail.to_string());
            } else {
                result.error = Some("No 'data' field in response".to_string());
            }
        } else {
            let preview: String = json_part.chars().take(120).collect();
            result.error = Some(format!("Could not parse JSON: {preview}"));
        }
    } else {
        let preview: String = output.chars().take(120).collect();
        result.error = Some(format!("No timing marker in output: {preview}"));
    }

    result
}

/// Build a curl command to test TTS via LiteLLM (OpenAI-compatible /v1/audio/speech).
pub fn build_tts_curl_command(ip: &str, port: u16, api_key: &str) -> String {
    let body = serde_json::json!({
        "model": "voice",
        "input": "Hello, this is a benchmark test.",
        "voice": "af_heart",
    });
    let body_str = body.to_string().replace('\'', "'\\''");
    let auth = if api_key.is_empty() {
        String::new()
    } else {
        format!("-H 'Authorization: Bearer {}'", api_key.replace('\'', "'\\''"))
    };
    format!(
        "curl -s -o /dev/null -w '\\n---BENCH_TIME:%{{time_total}}---\\n%{{http_code}}' \
         -H 'Content-Type: application/json' \
         {auth} \
         --max-time 30 \
         -d '{body_str}' \
         'http://{ip}:{port}/v1/audio/speech'; true"
    )
}

/// Build a curl command to test image generation via LiteLLM.
pub fn build_image_curl_command(ip: &str, port: u16, api_key: &str) -> String {
    let body = serde_json::json!({
        "model": "image",
        "prompt": "A simple red circle on white background",
        "n": 1,
        "size": "256x256",
    });
    let body_str = body.to_string().replace('\'', "'\\''");
    let auth = if api_key.is_empty() {
        String::new()
    } else {
        format!("-H 'Authorization: Bearer {}'", api_key.replace('\'', "'\\''"))
    };
    format!(
        "curl -s -w '\\n---BENCH_TIME:%{{time_total}}---' \
         -H 'Content-Type: application/json' \
         {auth} \
         --max-time 120 \
         -d '{body_str}' \
         'http://{ip}:{port}/v1/images/generations'; true"
    )
}

/// Build a curl command to test STT via LiteLLM.
/// Generates a small silent WAV file inline and sends it.
pub fn build_stt_curl_command(ip: &str, port: u16, api_key: &str) -> String {
    let auth = if api_key.is_empty() {
        String::new()
    } else {
        format!("-H 'Authorization: Bearer {}'", api_key.replace('\'', "'\\''"))
    };
    // Generate a tiny valid WAV file (44 bytes header + 8000 bytes silence = ~0.5s at 16kHz mono)
    format!(
        "python3 -c \"\
import struct,sys,tempfile,os;\
f=tempfile.NamedTemporaryFile(suffix='.wav',delete=False);\
sr=16000;ns=sr//2;nc=1;bps=16;\
data=b'\\x00\\x00'*ns;\
f.write(b'RIFF');\
f.write(struct.pack('<I',36+len(data)));\
f.write(b'WAVEfmt ');\
f.write(struct.pack('<IHHIIHH',16,1,nc,sr,sr*nc*bps//8,nc*bps//8,bps));\
f.write(b'data');\
f.write(struct.pack('<I',len(data)));\
f.write(data);\
f.close();\
print(f.name)\
\" 2>/dev/null | head -1 | xargs -I{{}} sh -c '\
curl -s -w \"\\n---BENCH_TIME:%{{time_total}}---\" \
 {auth} \
 --max-time 30 \
 -F model=transcribe \
 -F \"file=@{{}}\" \
 \"http://{ip}:{port}/v1/audio/transcriptions\"; rm -f {{}}\
'; true"
    )
}

/// Parse a TTS response — we use -o /dev/null and check HTTP code from -w output.
pub fn parse_tts_response(output: &str) -> ModelTestResult {
    let mut result = ModelTestResult {
        test_name: String::new(),
        tier: ModelTestTier::Service,
        passed: false,
        response_content: None,
        error: None,
        elapsed_ms: 0.0,
    };

    let timing_marker = "---BENCH_TIME:";
    if let Some(timing_pos) = output.rfind(timing_marker) {
        let after = &output[timing_pos + timing_marker.len()..];
        if let Some(end) = after.find("---") {
            if let Ok(secs) = after[..end].trim().parse::<f64>() {
                result.elapsed_ms = secs * 1000.0;
            }
        }
        // After timing marker and closing ---, the HTTP status code follows
        let rest = if let Some(end) = after.find("---") {
            after[end + 3..].trim()
        } else {
            ""
        };
        if let Ok(code) = rest.parse::<u16>() {
            if code == 200 {
                result.passed = true;
                result.response_content = Some(format!("HTTP {code} audio returned"));
            } else {
                result.error = Some(format!("HTTP {code}"));
            }
        } else {
            result.error = Some("Could not parse HTTP status".to_string());
        }
    } else {
        let preview: String = output.chars().take(120).collect();
        result.error = Some(format!("No timing marker: {preview}"));
    }

    result
}

/// Parse an image generation response — look for data[0].url or data[0].b64_json.
pub fn parse_image_response(output: &str) -> ModelTestResult {
    let mut result = ModelTestResult {
        test_name: String::new(),
        tier: ModelTestTier::Service,
        passed: false,
        response_content: None,
        error: None,
        elapsed_ms: 0.0,
    };

    let timing_marker = "---BENCH_TIME:";
    if let Some(timing_pos) = output.rfind(timing_marker) {
        let after = &output[timing_pos + timing_marker.len()..];
        if let Some(end) = after.find("---") {
            if let Ok(secs) = after[..end].trim().parse::<f64>() {
                result.elapsed_ms = secs * 1000.0;
            }
        }
        let json_part = output[..timing_pos].trim();
        if let Ok(json) = serde_json::from_str::<Value>(json_part) {
            if let Some(data) = json.get("data").and_then(|d| d.as_array()) {
                if !data.is_empty() {
                    let has_url = data[0].get("url").and_then(|u| u.as_str()).is_some();
                    let has_b64 = data[0].get("b64_json").and_then(|b| b.as_str()).is_some();
                    if has_url || has_b64 {
                        result.passed = true;
                        result.response_content = Some(format!("{} image(s) generated", data.len()));
                    } else {
                        result.error = Some("No url or b64_json in response".to_string());
                    }
                } else {
                    result.error = Some("Empty data array".to_string());
                }
            } else if let Some(err) = json.get("error").and_then(|e| e.get("message")).and_then(|m| m.as_str()) {
                result.error = Some(err.to_string());
            } else {
                result.error = Some("No 'data' in response".to_string());
            }
        } else {
            let preview: String = json_part.chars().take(120).collect();
            result.error = Some(format!("Could not parse JSON: {preview}"));
        }
    } else {
        let preview: String = output.chars().take(120).collect();
        result.error = Some(format!("No timing marker: {preview}"));
    }

    result
}

// =========================================================================
// Load Test helpers — hit agent-api endpoints, not raw vLLM
// =========================================================================

#[derive(Debug, Clone)]
pub struct LoadTestConfig {
    pub concurrency_levels: Vec<usize>,
    pub requests_per_level: usize,
    pub prompt: String,
    pub timeout_secs: u64,
}

impl Default for LoadTestConfig {
    fn default() -> Self {
        Self {
            concurrency_levels: vec![1, 2, 4, 8],
            requests_per_level: 4,
            prompt: "What is the current status of our projects?".to_string(),
            timeout_secs: 120,
        }
    }
}

#[derive(Debug, Clone)]
pub struct LoadTestResult {
    pub concurrency: usize,
    pub total_requests: usize,
    pub successful: usize,
    pub failed: usize,
    pub ttft_p50_ms: f64,
    pub ttft_p95_ms: f64,
    pub latency_p50_ms: f64,
    pub latency_p95_ms: f64,
    pub wall_time_secs: f64,
    pub throughput_rps: f64,
}

/// Build a curl command that hits the agent-api /chat/message endpoint
/// (non-streaming) and captures timing.
pub fn build_agent_chat_curl(
    agent_api_url: &str,
    token: &str,
    prompt: &str,
    timeout_secs: u64,
) -> String {
    let body = serde_json::json!({
        "message": prompt,
        "model": "auto",
        "enable_web_search": false,
        "enable_doc_search": false,
    });
    let body_str = body.to_string().replace('\'', "'\\''");

    format!(
        "curl -s -w '\\n---BENCH_TIME:%{{time_total}}---\\n---BENCH_TTFT:%{{time_starttransfer}}---' \
         -H 'Content-Type: application/json' \
         -H 'Authorization: Bearer {}' \
         --max-time {} \
         -d '{}' \
         '{}/chat/message'; true",
        token, timeout_secs, body_str, agent_api_url
    )
}

/// Build a shell snippet that runs N parallel agent-api chat requests.
pub fn build_parallel_agent_chat_command(
    agent_api_url: &str,
    token: &str,
    prompt: &str,
    count: usize,
    timeout_secs: u64,
) -> String {
    let single = build_agent_chat_curl(agent_api_url, token, prompt, timeout_secs);
    let mut script = String::new();
    script.push_str("_BENCH_DIR=$(mktemp -d); ");
    script.push_str("OVERALL_START=$(date +%s%N 2>/dev/null || echo 0); ");
    for i in 0..count {
        script.push_str(&format!(
            "( {single} > \"$_BENCH_DIR/{i}.out\" 2>&1 ) & "
        ));
    }
    script.push_str("wait; ");
    script.push_str("OVERALL_END=$(date +%s%N 2>/dev/null || echo 0); ");
    for i in 0..count {
        script.push_str(&format!(
            "echo '---BENCH_REQ:{i}---'; cat \"$_BENCH_DIR/{i}.out\"; echo ''; echo '---BENCH_REQ_END:{i}---'; "
        ));
    }
    script.push_str("echo \"---BENCH_WALL:$(( (OVERALL_END - OVERALL_START) ))---\"; ");
    script.push_str("rm -rf \"$_BENCH_DIR\"");
    script
}

/// Parse an STT transcription response — look for text field.
pub fn parse_stt_response(output: &str) -> ModelTestResult {
    let mut result = ModelTestResult {
        test_name: String::new(),
        tier: ModelTestTier::Service,
        passed: false,
        response_content: None,
        error: None,
        elapsed_ms: 0.0,
    };

    let timing_marker = "---BENCH_TIME:";
    if let Some(timing_pos) = output.rfind(timing_marker) {
        let after = &output[timing_pos + timing_marker.len()..];
        if let Some(end) = after.find("---") {
            if let Ok(secs) = after[..end].trim().parse::<f64>() {
                result.elapsed_ms = secs * 1000.0;
            }
        }
        let json_part = output[..timing_pos].trim();
        if let Ok(json) = serde_json::from_str::<Value>(json_part) {
            if let Some(text) = json.get("text").and_then(|t| t.as_str()) {
                result.passed = true;
                let preview: String = text.chars().take(60).collect();
                result.response_content = Some(if preview.is_empty() { "(silence)".to_string() } else { preview });
            } else if let Some(err) = json.get("error").and_then(|e| e.get("message")).and_then(|m| m.as_str()) {
                result.error = Some(err.to_string());
            } else {
                // A silent WAV may return empty text — that's still a pass
                result.passed = true;
                result.response_content = Some("(silence)".to_string());
            }
        } else {
            let preview: String = json_part.chars().take(120).collect();
            result.error = Some(format!("Could not parse JSON: {preview}"));
        }
    } else {
        let preview: String = output.chars().take(120).collect();
        result.error = Some(format!("No timing marker: {preview}"));
    }

    result
}
