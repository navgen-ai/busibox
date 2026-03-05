use serde_json::Value;

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
         'http://{}:{}/v1/chat/completions'",
        body_str, vllm_ip, port
    )
}

/// Build a shell snippet that runs N parallel curl commands and collects all output.
/// Each request's output is delimited by ---BENCH_REQ:N--- markers.
pub fn build_parallel_curl_command(
    vllm_ip: &str,
    port: u16,
    model_name: &str,
    prompt: &str,
    max_tokens: usize,
    count: usize,
) -> String {
    let single = build_curl_command(vllm_ip, port, model_name, prompt, max_tokens);
    let mut script = String::from("OVERALL_START=$(date +%s%N 2>/dev/null || echo 0); ");
    for i in 0..count {
        script.push_str(&format!(
            "( echo '---BENCH_REQ:{i}---'; {single}; echo ''; echo '---BENCH_REQ_END:{i}---' ) & "
        ));
    }
    script.push_str("wait; ");
    script.push_str("OVERALL_END=$(date +%s%N 2>/dev/null || echo 0); ");
    script.push_str("echo \"---BENCH_WALL:$(( (OVERALL_END - OVERALL_START) ))---\"");
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

    let completion_tokens = json
        .get("usage")
        .and_then(|u| u.get("completion_tokens"))
        .and_then(|t| t.as_u64())
        .unwrap_or(0) as usize;

    Some(CurlResponse {
        completion_tokens,
        elapsed_secs,
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
