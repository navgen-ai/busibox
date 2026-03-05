use crate::app::{App, BenchmarkUpdate, Screen};
use crate::modules::benchmark::{
    self, BenchmarkConfig, BenchmarkResult,
};
use crate::modules::models::{DeployedModel, LiveStatus};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::*;

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/// Populate the benchmark screen with running vLLM models from the deployed set.
/// If `preselect_port` is Some, toggle that model on by default.
pub fn init_screen(app: &mut App, preselect_port: Option<u16>) {
    let vllm_models: Vec<DeployedModel> = app
        .deployed_models
        .as_ref()
        .map(|ds| {
            ds.models
                .iter()
                .filter(|m| m.provider == "vllm" && m.assigned && m.port > 0)
                .cloned()
                .collect()
        })
        .unwrap_or_default();

    let count = vllm_models.len();
    let mut toggled = vec![false; count];

    if let Some(port) = preselect_port {
        for (i, m) in vllm_models.iter().enumerate() {
            if m.port == port {
                toggled[i] = true;
            }
        }
    }

    // If nothing pre-selected, select all running models
    if !toggled.iter().any(|&t| t) {
        for (i, m) in vllm_models.iter().enumerate() {
            if m.live_status == LiveStatus::Running {
                toggled[i] = true;
            }
        }
    }

    app.benchmark_models = vllm_models;
    app.benchmark_toggled = toggled;
    app.benchmark_selected = 0;
    app.benchmark_results = Vec::new();
    app.benchmark_log = Vec::new();
    app.benchmark_log_scroll = 0;
    app.benchmark_running = false;
    app.benchmark_complete = false;
    app.benchmark_tick = 0;
    app.benchmark_rx = None;
    app.benchmark_config = BenchmarkConfig::default();
}

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),  // title
            Constraint::Length(app.benchmark_models.len() as u16 + 4), // model selector
            Constraint::Length(3),  // config line
            Constraint::Min(6),    // results table + log
            Constraint::Length(2), // help bar
        ])
        .margin(2)
        .split(f.area());

    // Title
    let spinner_char = if app.benchmark_running {
        SPINNER[app.benchmark_tick % SPINNER.len()]
    } else {
        ""
    };

    let title = if app.benchmark_running {
        Paragraph::new(Line::from(vec![
            Span::styled(format!("{spinner_char} "), theme::info()),
            Span::styled("Running Benchmark...", theme::title()),
        ]))
    } else if app.benchmark_complete {
        Paragraph::new(Line::from(vec![
            Span::styled("✓ ", theme::success()),
            Span::styled("Benchmark Complete", theme::title()),
        ]))
    } else {
        Paragraph::new("Model Benchmark").style(theme::title())
    }
    .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    // Model selector
    render_model_selector(f, app, chunks[1]);

    // Config line
    let cfg = &app.benchmark_config;
    let config_line = Paragraph::new(Line::from(vec![
        Span::styled("  Config: ", theme::muted()),
        Span::styled(
            format!(
                "max_tokens={}/{}  parallel={}  runs={}",
                cfg.max_tokens_throughput, cfg.max_tokens_parallel, cfg.parallel_count, cfg.num_runs
            ),
            theme::info(),
        ),
    ]));
    f.render_widget(config_line, chunks[2]);

    // Results + log split
    let bottom_chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(results_table_height(app)),
            Constraint::Min(3),
        ])
        .split(chunks[3]);

    render_results_table(f, app, bottom_chunks[0]);
    render_log(f, app, bottom_chunks[1]);

    // Help bar
    let mut help_spans = vec![];
    if !app.benchmark_running {
        help_spans.extend_from_slice(&[
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Select  ", theme::normal()),
            Span::styled("Space ", theme::highlight()),
            Span::styled("Toggle  ", theme::normal()),
        ]);
        let has_selected = app.benchmark_toggled.iter().any(|&t| t);
        if has_selected {
            help_spans.extend_from_slice(&[
                Span::styled("Enter ", theme::highlight()),
                Span::styled("Run  ", theme::success()),
            ]);
        }
        help_spans.extend_from_slice(&[
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]);
    } else {
        help_spans.extend_from_slice(&[
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Scroll Log  ", theme::normal()),
            Span::styled("(running...)", theme::muted()),
        ]);
    }

    let help = Paragraph::new(Line::from(help_spans));
    f.render_widget(help, chunks[4]);
}

fn render_model_selector(f: &mut Frame, app: &App, area: Rect) {
    let inner = area.inner(Margin::new(1, 0));

    if app.benchmark_models.is_empty() {
        let msg = Paragraph::new(Line::from(Span::styled(
            "  No deployed vLLM models found. Deploy models first.",
            theme::warning(),
        )))
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Models ")
                .title_style(theme::heading()),
        );
        f.render_widget(msg, area);
        return;
    }

    let mut lines: Vec<Line> = Vec::new();

    for (i, model) in app.benchmark_models.iter().enumerate() {
        let is_selected = i == app.benchmark_selected;
        let is_toggled = app.benchmark_toggled.get(i).copied().unwrap_or(false);

        let checkbox = if is_toggled { "[x]" } else { "[ ]" };
        let status_indicator = match &model.live_status {
            LiveStatus::Running => Span::styled(" ● ", theme::success()),
            LiveStatus::Down => Span::styled(" ○ ", theme::error()),
            LiveStatus::Error(_) => Span::styled(" ✗ ", theme::error()),
            _ => Span::styled(" ? ", theme::muted()),
        };

        let name_style = if is_selected && !app.benchmark_running {
            theme::selected()
        } else if is_toggled {
            theme::info()
        } else {
            theme::normal()
        };

        let detail_style = if is_selected && !app.benchmark_running {
            theme::selected()
        } else {
            theme::muted()
        };

        lines.push(Line::from(vec![
            Span::styled(format!("  {checkbox} "), name_style),
            status_indicator,
            Span::styled(&model.model_key, name_style),
            Span::styled(
                format!("  (port {}, GPU {})", model.port, model.gpu),
                detail_style,
            ),
        ]));
    }

    let para = Paragraph::new(lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Models ")
            .title_style(theme::heading()),
    );
    f.render_widget(para, area);

    // Render in inner if needed for scrollbar, but model lists are typically small
    let _ = inner;
}

fn results_table_height(app: &App) -> u16 {
    if app.benchmark_results.is_empty() {
        0
    } else {
        (app.benchmark_results.len() as u16 + 4).min(12)
    }
}

fn render_results_table(f: &mut Frame, app: &App, area: Rect) {
    if app.benchmark_results.is_empty() {
        return;
    }

    let header = Row::new(vec![
        Cell::from(Span::styled("Model", theme::heading())),
        Cell::from(Span::styled("Port", theme::heading())),
        Cell::from(Span::styled("TTFT", theme::heading())),
        Cell::from(Span::styled("Throughput", theme::heading())),
        Cell::from(Span::styled("Parallel", theme::heading())),
        Cell::from(Span::styled("P.Latency", theme::heading())),
    ])
    .height(1);

    let rows: Vec<Row> = app
        .benchmark_results
        .iter()
        .map(|r| {
            let name = if r.model_name.len() > 25 {
                format!("{}…", &r.model_name[..24])
            } else {
                r.model_name.clone()
            };

            let ttft = r
                .ttft_ms
                .map(|v| format!("{:.0} ms", v))
                .unwrap_or_else(|| "—".into());
            let throughput = r
                .throughput_tps
                .map(|v| format!("{:.1} tok/s", v))
                .unwrap_or_else(|| "—".into());
            let parallel = r
                .parallel_tps
                .map(|v| format!("{:.1} tok/s", v))
                .unwrap_or_else(|| "—".into());
            let p_latency = r
                .parallel_latency_ms
                .map(|v| format!("{:.0} ms", v))
                .unwrap_or_else(|| "—".into());

            Row::new(vec![
                Cell::from(Span::styled(name, theme::info())),
                Cell::from(Span::styled(r.port.to_string(), theme::muted())),
                Cell::from(Span::styled(ttft, theme::normal())),
                Cell::from(Span::styled(throughput, theme::success())),
                Cell::from(Span::styled(parallel, theme::info())),
                Cell::from(Span::styled(p_latency, theme::muted())),
            ])
        })
        .collect();

    let widths = [
        Constraint::Min(20),
        Constraint::Length(6),
        Constraint::Length(10),
        Constraint::Length(14),
        Constraint::Length(14),
        Constraint::Length(10),
    ];

    let table = Table::new(rows, widths)
        .header(header)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Results ")
                .title_style(theme::heading()),
        )
        .row_highlight_style(theme::highlight());

    f.render_widget(table, area);
}

fn render_log(f: &mut Frame, app: &App, area: Rect) {
    if app.benchmark_log.is_empty() && !app.benchmark_running {
        return;
    }

    let log_lines: Vec<Line> = app
        .benchmark_log
        .iter()
        .map(|line| {
            let style = if line.starts_with("ERROR") || line.contains("failed") {
                theme::error()
            } else if line.starts_with('✓') || line.contains("success") {
                theme::success()
            } else if line.starts_with(">>>") || line.starts_with("---") {
                theme::heading()
            } else {
                theme::normal()
            };
            Line::from(Span::styled(line.as_str(), style))
        })
        .collect();

    let visible_height = area.height.saturating_sub(2) as usize;
    let total = log_lines.len();
    let offset = if total > visible_height {
        app.benchmark_log_scroll
            .min(total.saturating_sub(visible_height))
    } else {
        0
    };

    let paragraph = Paragraph::new(log_lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Log ")
                .title_style(theme::heading()),
        )
        .scroll((offset as u16, 0))
        .wrap(Wrap { trim: false });
    f.render_widget(paragraph, area);

    if total > visible_height {
        let mut scrollbar_state =
            ScrollbarState::new(total.saturating_sub(visible_height)).position(offset);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            area.inner(Margin::new(0, 1)),
            &mut scrollbar_state,
        );
    }
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.benchmark_running {
        match key.code {
            KeyCode::Up => {
                app.benchmark_log_scroll = app.benchmark_log_scroll.saturating_sub(1);
            }
            KeyCode::Down => {
                let max = app.benchmark_log.len().saturating_sub(1);
                if app.benchmark_log_scroll < max {
                    app.benchmark_log_scroll += 1;
                }
            }
            _ => {}
        }
        return;
    }

    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::ModelsManage;
        }
        KeyCode::Up => {
            if !app.benchmark_models.is_empty() && app.benchmark_selected > 0 {
                app.benchmark_selected -= 1;
            }
        }
        KeyCode::Down => {
            if app.benchmark_selected + 1 < app.benchmark_models.len() {
                app.benchmark_selected += 1;
            }
        }
        KeyCode::Char(' ') => {
            if let Some(toggled) = app.benchmark_toggled.get_mut(app.benchmark_selected) {
                *toggled = !*toggled;
            }
        }
        KeyCode::Enter => {
            let has_selected = app.benchmark_toggled.iter().any(|&t| t);
            if has_selected && !app.benchmark_models.is_empty() {
                start_benchmark(app);
            }
        }
        _ => {}
    }
}

fn start_benchmark(app: &mut App) {
    let selected_models: Vec<DeployedModel> = app
        .benchmark_models
        .iter()
        .enumerate()
        .filter(|(i, _)| app.benchmark_toggled.get(*i).copied().unwrap_or(false))
        .map(|(_, m)| m.clone())
        .collect();

    if selected_models.is_empty() {
        return;
    }

    app.benchmark_running = true;
    app.benchmark_complete = false;
    app.benchmark_results.clear();
    app.benchmark_log.clear();
    app.benchmark_log_scroll = 0;
    app.benchmark_tick = 0;

    let config = app.benchmark_config.clone();

    let is_remote = app
        .active_profile()
        .map(|(_, p)| p.remote)
        .unwrap_or(false);

    let is_proxmox = app
        .active_profile()
        .map(|(_, p)| p.backend == "proxmox")
        .unwrap_or(false);

    let ssh_details: Option<(String, String, String)> = if is_remote {
        app.active_profile().and_then(|(_, p)| {
            p.effective_host().map(|host| {
                (
                    host.to_string(),
                    p.effective_user().to_string(),
                    p.effective_ssh_key().to_string(),
                )
            })
        })
    } else {
        None
    };

    let vllm_network_base: String = app
        .active_profile()
        .map(|(_, p)| p.vllm_network_base().to_string())
        .unwrap_or_else(|| "10.96.200".to_string());

    let (tx, rx) = std::sync::mpsc::channel();
    app.benchmark_rx = Some(rx);

    std::thread::spawn(move || {
        let vllm_ip = if is_proxmox {
            format!("{vllm_network_base}.208")
        } else {
            "localhost".to_string()
        };

        for model in &selected_models {
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                ">>> Benchmarking {} (port {})",
                model.model_key, model.port
            )));

            let mut result = BenchmarkResult::new(&model.model_name, model.port);

            // --- TTFT Test ---
            let _ = tx.send(BenchmarkUpdate::Log(
                "--- TTFT Test (max_tokens=1) ---".into(),
            ));
            let mut ttft_values: Vec<f64> = Vec::new();
            for run in 1..=config.num_runs {
                let _ = tx.send(BenchmarkUpdate::Log(format!(
                    "  Run {run}/{}...",
                    config.num_runs
                )));
                let curl_cmd = benchmark::build_curl_command(
                    &vllm_ip,
                    model.port,
                    &model.model_name,
                    &config.prompt,
                    1,
                );
                match exec_curl(&curl_cmd, is_remote, &ssh_details) {
                    Ok(output) => {
                        if let Some(resp) = benchmark::parse_curl_response(&output) {
                            let ms = resp.elapsed_secs * 1000.0;
                            ttft_values.push(ms);
                            let _ = tx.send(BenchmarkUpdate::Log(format!(
                                "  ✓ {:.0} ms ({} tokens)",
                                ms, resp.completion_tokens
                            )));
                        } else {
                            let _ = tx.send(BenchmarkUpdate::Log(
                                "  ERROR: Could not parse response".into(),
                            ));
                            let preview: String = output.chars().take(200).collect();
                            let _ = tx.send(BenchmarkUpdate::Log(format!(
                                "  Response preview: {preview}"
                            )));
                        }
                    }
                    Err(e) => {
                        let _ = tx.send(BenchmarkUpdate::Log(format!("  ERROR: {e}")));
                    }
                }
            }
            if !ttft_values.is_empty() {
                result.ttft_ms = Some(benchmark::median(&mut ttft_values));
            }

            // --- Throughput Test ---
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                "--- Throughput Test (max_tokens={}) ---",
                config.max_tokens_throughput
            )));
            let mut tps_values: Vec<f64> = Vec::new();
            for run in 1..=config.num_runs {
                let _ = tx.send(BenchmarkUpdate::Log(format!(
                    "  Run {run}/{}...",
                    config.num_runs
                )));
                let curl_cmd = benchmark::build_curl_command(
                    &vllm_ip,
                    model.port,
                    &model.model_name,
                    &config.prompt,
                    config.max_tokens_throughput,
                );
                match exec_curl(&curl_cmd, is_remote, &ssh_details) {
                    Ok(output) => {
                        if let Some(resp) = benchmark::parse_curl_response(&output) {
                            if resp.elapsed_secs > 0.0 && resp.completion_tokens > 0 {
                                let tps =
                                    resp.completion_tokens as f64 / resp.elapsed_secs;
                                tps_values.push(tps);
                                let _ = tx.send(BenchmarkUpdate::Log(format!(
                                    "  ✓ {:.1} tok/s ({} tokens in {:.2}s)",
                                    tps, resp.completion_tokens, resp.elapsed_secs
                                )));
                            } else {
                                let _ = tx.send(BenchmarkUpdate::Log(
                                    "  WARNING: 0 tokens or 0 time".into(),
                                ));
                            }
                        } else {
                            let _ = tx.send(BenchmarkUpdate::Log(
                                "  ERROR: Could not parse response".into(),
                            ));
                        }
                    }
                    Err(e) => {
                        let _ = tx.send(BenchmarkUpdate::Log(format!("  ERROR: {e}")));
                    }
                }
            }
            if !tps_values.is_empty() {
                result.throughput_tps = Some(benchmark::median(&mut tps_values));
            }

            // --- Parallel Test ---
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                "--- Parallel Test ({} concurrent, max_tokens={}) ---",
                config.parallel_count, config.max_tokens_parallel
            )));
            let parallel_cmd = benchmark::build_parallel_curl_command(
                &vllm_ip,
                model.port,
                &model.model_name,
                &config.prompt,
                config.max_tokens_parallel,
                config.parallel_count,
            );
            match exec_curl(&parallel_cmd, is_remote, &ssh_details) {
                Ok(output) => {
                    let (responses, wall_ns) = benchmark::parse_parallel_output(&output);
                    if responses.is_empty() {
                        let _ = tx.send(BenchmarkUpdate::Log(
                            "  ERROR: No valid responses from parallel test".into(),
                        ));
                    } else {
                        let total_tokens: usize =
                            responses.iter().map(|r| r.completion_tokens).sum();
                        let mut latencies: Vec<f64> = responses
                            .iter()
                            .map(|r| r.elapsed_secs * 1000.0)
                            .collect();
                        let median_latency = benchmark::median(&mut latencies);

                        // Use wall clock if available, otherwise max individual time
                        let wall_secs = wall_ns
                            .map(|ns| ns as f64 / 1_000_000_000.0)
                            .unwrap_or_else(|| {
                                responses
                                    .iter()
                                    .map(|r| r.elapsed_secs)
                                    .fold(0.0_f64, f64::max)
                            });

                        let agg_tps = if wall_secs > 0.0 {
                            total_tokens as f64 / wall_secs
                        } else {
                            0.0
                        };

                        result.parallel_tps = Some(agg_tps);
                        result.parallel_latency_ms = Some(median_latency);

                        let _ = tx.send(BenchmarkUpdate::Log(format!(
                            "  ✓ {}/{} responses, {} total tokens",
                            responses.len(),
                            config.parallel_count,
                            total_tokens
                        )));
                        let _ = tx.send(BenchmarkUpdate::Log(format!(
                            "  ✓ {:.1} agg tok/s, {:.0} ms median latency",
                            agg_tps, median_latency
                        )));
                    }
                }
                Err(e) => {
                    let _ = tx.send(BenchmarkUpdate::Log(format!("  ERROR: {e}")));
                }
            }

            let _ = tx.send(BenchmarkUpdate::Result(result));
        }

        let _ = tx.send(BenchmarkUpdate::Log(
            "✓ Benchmark complete".into(),
        ));
        let _ = tx.send(BenchmarkUpdate::Complete);
    });
}

/// Execute a curl command locally or via SSH, returning the raw output.
fn exec_curl(
    cmd: &str,
    is_remote: bool,
    ssh_details: &Option<(String, String, String)>,
) -> Result<String, String> {
    if is_remote {
        if let Some((ref host, ref user, ref key)) = ssh_details {
            let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
            let full_cmd = format!(
                "{}{}",
                crate::modules::remote::SHELL_PATH_PREAMBLE,
                cmd
            );
            ssh.run(&full_cmd)
                .map_err(|e| format!("SSH exec failed: {e}"))
        } else {
            Err("No SSH credentials".into())
        }
    } else {
        let output = std::process::Command::new("sh")
            .arg("-c")
            .arg(cmd)
            .output()
            .map_err(|e| format!("exec failed: {e}"))?;

        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        if !stderr.trim().is_empty() && !output.status.success() {
            Err(format!("curl failed: {stderr}"))
        } else {
            Ok(stdout)
        }
    }
}
