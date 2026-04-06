use crate::app::{App, BenchmarkUpdate, CloudKeysUpdate, Screen};
use crate::modules::benchmark::{
    self, BenchmarkConfig, BenchmarkMode, BenchmarkResult, ModelTestTier,
};
use crate::modules::models::{DeployedModel, LiveStatus};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::*;

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/// Populate the benchmark screen with deployed LLM models (vLLM or MLX).
/// If `preselect_port` is Some, toggle that model on by default.
pub fn init_screen(app: &mut App, preselect_port: Option<u16>) {
    let llm_models: Vec<DeployedModel> = app
        .deployed_models
        .as_ref()
        .map(|ds| {
            ds.models
                .iter()
                .filter(|m| {
                    (m.provider == "vllm" || m.provider == "mlx")
                        && m.assigned
                        && m.port > 0
                })
                .cloned()
                .collect()
        })
        .unwrap_or_default();

    let count = llm_models.len();
    let mut toggled = vec![false; count];

    if let Some(port) = preselect_port {
        for (i, m) in llm_models.iter().enumerate() {
            if m.port == port {
                toggled[i] = true;
            }
        }
    }

    if !toggled.iter().any(|&t| t) {
        for (i, m) in llm_models.iter().enumerate() {
            if m.live_status == LiveStatus::Running {
                toggled[i] = true;
            }
        }
    }

    app.benchmark_models = llm_models;
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
    app.benchmark_model_test_results = Vec::new();
}

pub fn render(f: &mut Frame, app: &App) {
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),  // spacer for profile infobar overlay
            Constraint::Length(2),  // tab bar
            Constraint::Min(0),     // content
        ])
        .split(f.area());
    render_tab_bar(f, app, outer[1]);
    match app.benchmark_mode {
        BenchmarkMode::Performance => render_performance(f, app, outer[2]),
        BenchmarkMode::ModelTests => render_model_tests(f, app, outer[2]),
        BenchmarkMode::LoadTest => render_load_test(f, app, outer[2]),
        BenchmarkMode::CloudKeys => render_cloud_keys(f, app, outer[2]),
    }
}

fn render_tab_bar(f: &mut Frame, app: &App, area: Rect) {
    let tabs: &[(&str, BenchmarkMode)] = &[
        (" Benchmark ", BenchmarkMode::Performance),
        (" Model Tests ", BenchmarkMode::ModelTests),
        (" Load Test ", BenchmarkMode::LoadTest),
        (" Cloud Keys ", BenchmarkMode::CloudKeys),
    ];
    let mut spans: Vec<Span> = Vec::new();
    for (label, mode) in tabs {
        let active = app.benchmark_mode == *mode;
        let style = if active {
            theme::highlight()
                .add_modifier(ratatui::style::Modifier::BOLD)
                .add_modifier(ratatui::style::Modifier::UNDERLINED)
        } else {
            theme::muted()
        };
        let prefix = if active { "▶ " } else { "  " };
        spans.push(Span::styled(format!("{prefix}{label}"), style));
        spans.push(Span::styled("  │  ", theme::dim()));
    }
    spans.push(Span::styled(" Tab to switch", theme::dim()));
    let para = Paragraph::new(Line::from(spans))
        .block(Block::default().borders(Borders::BOTTOM).border_style(theme::dim()));
    f.render_widget(para, area);
}

fn render_load_test(f: &mut Frame, app: &App, area: Rect) {
    let show_model_picker = app.load_test_level == 0 && !app.benchmark_models.is_empty();
    let model_rows = if show_model_picker {
        (app.benchmark_models.len() as u16 + 2).min(8)
    } else {
        0
    };

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),           // level selector
            Constraint::Length(model_rows),   // model picker (engine only)
            Constraint::Length(5),           // info
            Constraint::Min(6),             // log
        ])
        .split(area);

    // Level selector bar
    const LEVELS: &[&str] = &[" Engine ", " LiteLLM ", " Agent-API "];
    let mut level_spans: Vec<Span> = Vec::new();
    for (i, label) in LEVELS.iter().enumerate() {
        let style = if i == app.load_test_level { theme::highlight() } else { theme::dim() };
        level_spans.push(Span::styled(*label, style));
        if i + 1 < LEVELS.len() {
            level_spans.push(Span::styled(" │ ", theme::muted()));
        }
    }
    let status_span = if app.benchmark_running {
        Span::styled(
            format!("   {} Running...", SPINNER[app.benchmark_tick % SPINNER.len()]),
            theme::warning(),
        )
    } else if app.benchmark_complete {
        Span::styled("   ✓ Complete", theme::success())
    } else {
        Span::styled("   ◀ ▶ select  Enter to start", theme::dim())
    };
    level_spans.push(status_span);

    let selector = Paragraph::new(Line::from(level_spans))
        .block(Block::default().borders(Borders::ALL).title(" Load Test Level ").border_style(theme::dim()));
    f.render_widget(selector, chunks[0]);

    // Model picker (engine level only)
    if show_model_picker {
        let model_lines: Vec<Line> = app.benchmark_models.iter().enumerate().map(|(i, m)| {
            let marker = if i == app.load_test_model_idx { "▸ " } else { "  " };
            let style = if i == app.load_test_model_idx { theme::highlight() } else { theme::dim() };
            Line::from(Span::styled(
                format!("{marker}{} ({}, port {})", m.model_name, m.provider.to_uppercase(), m.port),
                style,
            ))
        }).collect();
        let model_block = Paragraph::new(model_lines)
            .block(Block::default().borders(Borders::ALL).title(" Model ▲▼ ").border_style(theme::dim()));
        f.render_widget(model_block, chunks[1]);
    }

    // Per-level info
    let info_lines: Vec<Line> = match app.load_test_level {
        0 => {
            let model_hint = app.benchmark_models.get(app.load_test_model_idx)
                .map(|m| format!("  Selected: {} ({})", m.model_name, m.provider.to_uppercase()))
                .unwrap_or_else(|| "  No models loaded.".to_string());
            vec![
                Line::from("  Hits model engine /v1/chat/completions — queries /v1/models for served name."),
                Line::from(model_hint),
                Line::from("  No authentication required. Sustains each concurrency level for 30s."),
            ]
        }
        1 => vec![
            Line::from("  Hits LiteLLM proxy at /v1/chat/completions."),
            Line::from("  Target: .207:4000 on Proxmox / localhost:4000 on Docker."),
            Line::from("  Auth: litellm_api_key read automatically from vault. Sustains for 30s."),
        ],
        _ => vec![
            Line::from("  Hits agent-api /chat/message at concurrency 1, 2, 4, 8."),
            Line::from("  Target: .202:8000 on Proxmox / localhost:8000 on Docker."),
            Line::from("  Auth: delegation token from vault → exchanged for JWT via authz. Sustains for 30s."),
        ],
    };
    let info = Paragraph::new(info_lines)
        .block(Block::default().borders(Borders::ALL).title(" Info ").border_style(theme::dim()));
    f.render_widget(info, chunks[2]);

    let log_height = chunks[3].height.saturating_sub(2) as usize;
    let total = app.benchmark_log.len();
    let skip = if total > log_height {
        total - log_height - app.benchmark_log_scroll.min(total.saturating_sub(log_height))
    } else {
        0
    };
    let log_lines: Vec<Line> = app
        .benchmark_log
        .iter()
        .skip(skip)
        .take(log_height)
        .map(|s| Line::from(s.as_str()))
        .collect();

    let log = Paragraph::new(log_lines)
        .block(Block::default().borders(Borders::ALL).title(" Log ").border_style(theme::dim()));
    f.render_widget(log, chunks[3]);
}

fn render_performance(f: &mut Frame, app: &App, area: Rect) {
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
        .split(area);

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
        Paragraph::new(Line::from(vec![
            Span::styled("Model Benchmark", theme::title()),
            Span::styled("  [Performance]", theme::info()),
        ]))
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
    render_help_bar(f, app, chunks[4]);
}

fn render_model_tests(f: &mut Frame, app: &App, area: Rect) {
    let results_height = if app.benchmark_model_test_results.is_empty() {
        0u16
    } else {
        // Allow up to half the screen height for results table, min 6 rows for log
        let max_table = area.height.saturating_sub(6 + 2 + 3 + 2); // log+help+title+margin
        (app.benchmark_model_test_results.len() as u16 + 4).min(max_table.max(8))
    };

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),         // title
            Constraint::Length(results_height), // test results table
            Constraint::Min(3),            // log
            Constraint::Length(2),         // help bar
        ])
        .margin(2)
        .split(area);

    let spinner_char = if app.benchmark_running {
        SPINNER[app.benchmark_tick % SPINNER.len()]
    } else {
        ""
    };

    let title = if app.benchmark_running {
        Paragraph::new(Line::from(vec![
            Span::styled(format!("{spinner_char} "), theme::info()),
            Span::styled("Running Model Tests...", theme::title()),
        ]))
    } else if app.benchmark_complete {
        let passed = app.benchmark_model_test_results.iter().filter(|r| r.passed).count();
        let total = app.benchmark_model_test_results.len();
        let style = if passed == total { theme::success() } else { theme::warning() };
        Paragraph::new(Line::from(vec![
            Span::styled(format!("{passed}/{total} Passed"), style),
            Span::styled("  Model Tests Complete", theme::title()),
        ]))
    } else {
        Paragraph::new(Line::from(vec![
            Span::styled("Model Tests", theme::title()),
            Span::styled("  [Model Tests]", theme::info()),
        ]))
    }
    .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    // Test results table
    render_model_test_results(f, app, chunks[1]);

    // Log
    render_log(f, app, chunks[2]);

    // Help bar
    render_help_bar(f, app, chunks[3]);
}

fn render_model_test_results(f: &mut Frame, app: &App, area: Rect) {
    if app.benchmark_model_test_results.is_empty() {
        return;
    }

    let header = Row::new(vec![
        Cell::from(Span::styled("Test", theme::heading())),
        Cell::from(Span::styled("Tier", theme::heading())),
        Cell::from(Span::styled("Status", theme::heading())),
        Cell::from(Span::styled("Time", theme::heading())),
        Cell::from(Span::styled("Response / Error", theme::heading())),
    ])
    .height(1);

    let rows: Vec<Row> = app
        .benchmark_model_test_results
        .iter()
        .map(|r| {
            let name = if r.test_name.len() > 20 {
                format!("{}…", &r.test_name[..19])
            } else {
                r.test_name.clone()
            };

            let (status_str, status_style) = if r.passed {
                ("PASS", theme::success())
            } else {
                ("FAIL", theme::error())
            };

            let detail = if let Some(ref content) = r.response_content {
                let truncated: String = content.chars().take(40).collect();
                truncated
            } else if let Some(ref err) = r.error {
                let truncated: String = err.chars().take(40).collect();
                truncated
            } else {
                "—".to_string()
            };

            Row::new(vec![
                Cell::from(Span::styled(name, theme::info())),
                Cell::from(Span::styled(r.tier.to_string(), theme::muted())),
                Cell::from(Span::styled(status_str, status_style)),
                Cell::from(Span::styled(format!("{:.0}ms", r.elapsed_ms), theme::normal())),
                Cell::from(Span::styled(detail, theme::normal())),
            ])
        })
        .collect();

    let widths = [
        Constraint::Min(15),
        Constraint::Length(8),
        Constraint::Length(6),
        Constraint::Length(8),
        Constraint::Min(20),
    ];

    let table = Table::new(rows, widths)
        .header(header)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Test Results ")
                .title_style(theme::heading()),
        )
        .row_highlight_style(theme::highlight());

    f.render_widget(table, area);
}

fn render_help_bar(f: &mut Frame, app: &App, area: Rect) {
    let mut help_spans = vec![];
    if !app.benchmark_running {
        if app.benchmark_mode == BenchmarkMode::Performance {
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
        } else {
            help_spans.extend_from_slice(&[
                Span::styled("Enter ", theme::highlight()),
                Span::styled("Run Tests  ", theme::success()),
            ]);
        }
        help_spans.extend_from_slice(&[
            Span::styled("Tab ", theme::highlight()),
            Span::styled("Switch Mode  ", theme::normal()),
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
    f.render_widget(help, area);
}

fn render_model_selector(f: &mut Frame, app: &App, area: Rect) {
    let inner = area.inner(Margin::new(1, 0));

    if app.benchmark_models.is_empty() {
        let msg = Paragraph::new(Line::from(Span::styled(
            "  No deployed LLM models found. Deploy models first.",
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
    ])
    .height(1);

    let rows: Vec<Row> = app
        .benchmark_results
        .iter()
        .map(|r| {
            let name = if r.model_name.len() > 30 {
                format!("{}…", &r.model_name[..29])
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

            Row::new(vec![
                Cell::from(Span::styled(name, theme::info())),
                Cell::from(Span::styled(r.port.to_string(), theme::muted())),
                Cell::from(Span::styled(ttft, theme::normal())),
                Cell::from(Span::styled(throughput, theme::success())),
            ])
        })
        .collect();

    let widths = [
        Constraint::Min(25),
        Constraint::Length(6),
        Constraint::Length(10),
        Constraint::Length(14),
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
        if app.benchmark_running {
            // Auto-scroll to show the latest entries while running
            total.saturating_sub(visible_height)
        } else {
            app.benchmark_log_scroll
                .min(total.saturating_sub(visible_height))
        }
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
        KeyCode::Tab | KeyCode::BackTab => {
            // If editing in CloudKeys, Tab moves between fields instead of switching tabs
            if app.benchmark_mode == BenchmarkMode::CloudKeys && app.cloud_keys_editing {
                app.cloud_keys_editing = false;
                return;
            }
            app.benchmark_mode = match app.benchmark_mode {
                BenchmarkMode::Performance => BenchmarkMode::ModelTests,
                BenchmarkMode::ModelTests => BenchmarkMode::LoadTest,
                BenchmarkMode::LoadTest => BenchmarkMode::CloudKeys,
                BenchmarkMode::CloudKeys => BenchmarkMode::Performance,
            };
            // Clear results when switching modes
            app.benchmark_results.clear();
            app.benchmark_model_test_results.clear();
            app.benchmark_log.clear();
            app.benchmark_log_scroll = 0;
            app.benchmark_complete = false;
        }
        KeyCode::Up => {
            match app.benchmark_mode {
                BenchmarkMode::Performance => {
                    if !app.benchmark_models.is_empty() && app.benchmark_selected > 0 {
                        app.benchmark_selected -= 1;
                    }
                }
                BenchmarkMode::LoadTest if app.load_test_level == 0 => {
                    if app.load_test_model_idx > 0 {
                        app.load_test_model_idx -= 1;
                    }
                }
                BenchmarkMode::CloudKeys if !app.cloud_keys_editing => {
                    if app.cloud_keys_field > 0 {
                        app.cloud_keys_field -= 1;
                    }
                }
                _ => {}
            }
        }
        KeyCode::Down => {
            match app.benchmark_mode {
                BenchmarkMode::Performance => {
                    if app.benchmark_selected + 1 < app.benchmark_models.len() {
                        app.benchmark_selected += 1;
                    }
                }
                BenchmarkMode::LoadTest if app.load_test_level == 0 => {
                    if app.load_test_model_idx + 1 < app.benchmark_models.len() {
                        app.load_test_model_idx += 1;
                    }
                }
                BenchmarkMode::CloudKeys if !app.cloud_keys_editing => {
                    if app.cloud_keys_field < 3 {
                        app.cloud_keys_field += 1;
                    }
                }
                _ => {}
            }
        }
        KeyCode::Left | KeyCode::Right => {
            if app.benchmark_mode == BenchmarkMode::LoadTest && !app.benchmark_running {
                app.load_test_level = match key.code {
                    KeyCode::Left => app.load_test_level.saturating_sub(1),
                    _ => (app.load_test_level + 1).min(2),
                };
                app.benchmark_log.clear();
                app.benchmark_complete = false;
            }
        }
        KeyCode::Enter => {
            match app.benchmark_mode {
                BenchmarkMode::Performance => {
                    let has_selected = app.benchmark_toggled.iter().any(|&t| t);
                    if has_selected && !app.benchmark_models.is_empty() {
                        start_benchmark(app);
                    }
                }
                BenchmarkMode::ModelTests => {
                    start_model_tests(app);
                }
                BenchmarkMode::LoadTest => {
                    start_load_test(app);
                }
                BenchmarkMode::CloudKeys => {
                    app.cloud_keys_editing = !app.cloud_keys_editing;
                }
            }
        }
        KeyCode::Char(c) => {
            if app.benchmark_mode == BenchmarkMode::CloudKeys {
                if app.cloud_keys_editing {
                    match app.cloud_keys_field {
                        0 => app.cloud_keys_openai.push(c),
                        1 => app.cloud_keys_bedrock_access.push(c),
                        2 => app.cloud_keys_bedrock_secret.push(c),
                        3 => app.cloud_keys_bedrock_region.push(c),
                        _ => {}
                    }
                } else if c == 's' || c == 'S' {
                    if !app.cloud_keys_saving {
                        save_cloud_keys(app);
                    }
                }
            } else if c == ' ' && app.benchmark_mode == BenchmarkMode::Performance {
                if let Some(toggled) = app.benchmark_toggled.get_mut(app.benchmark_selected) {
                    *toggled = !*toggled;
                }
            }
        }
        KeyCode::Backspace => {
            if app.benchmark_mode == BenchmarkMode::CloudKeys && app.cloud_keys_editing {
                match app.cloud_keys_field {
                    0 => { app.cloud_keys_openai.pop(); }
                    1 => { app.cloud_keys_bedrock_access.pop(); }
                    2 => { app.cloud_keys_bedrock_secret.pop(); }
                    3 => { app.cloud_keys_bedrock_region.pop(); }
                    _ => {}
                }
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
            // MLX always runs on localhost; vLLM uses the network IP
            let model_ip = if model.provider == "mlx" {
                "localhost".to_string()
            } else {
                vllm_ip.clone()
            };

            let provider_label = model.provider.to_uppercase();
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                ">>> Benchmarking {} [{}] (port {})",
                model.model_key, provider_label, model.port
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
                    &model_ip,
                    model.port,
                    model.api_model_name(),
                    &config.prompt,
                    1,
                );
                match exec_curl(&curl_cmd, is_remote, &ssh_details) {
                    Ok(output) => {
                        if let Some(resp) = benchmark::parse_curl_response(&output) {
                            if let Some(ref err) = resp.error_message {
                                let _ = tx.send(BenchmarkUpdate::Log(format!(
                                    "  ERROR: API error: {err}"
                                )));
                            } else {
                                let ms = resp.elapsed_secs * 1000.0;
                                ttft_values.push(ms);
                                let _ = tx.send(BenchmarkUpdate::Log(format!(
                                    "  ✓ {:.0} ms ({} tokens)",
                                    ms, resp.completion_tokens
                                )));
                            }
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
                    &model_ip,
                    model.port,
                    model.api_model_name(),
                    &config.prompt,
                    config.max_tokens_throughput,
                );
                match exec_curl(&curl_cmd, is_remote, &ssh_details) {
                    Ok(output) => {
                        if let Some(resp) = benchmark::parse_curl_response(&output) {
                            if let Some(ref err) = resp.error_message {
                                let _ = tx.send(BenchmarkUpdate::Log(format!(
                                    "  ERROR: API error: {err}"
                                )));
                            } else if resp.elapsed_secs > 0.0 && resp.completion_tokens > 0 {
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

            let _ = tx.send(BenchmarkUpdate::Result(result));
        }

        let _ = tx.send(BenchmarkUpdate::Log(
            "✓ Benchmark complete".into(),
        ));
        let _ = tx.send(BenchmarkUpdate::Complete);
    });
}

fn start_model_tests(app: &mut App) {
    app.benchmark_running = true;
    app.benchmark_complete = false;
    app.benchmark_model_test_results.clear();
    app.benchmark_log.clear();
    app.benchmark_log_scroll = 0;
    app.benchmark_tick = 0;

    let selected_models: Vec<DeployedModel> = app
        .benchmark_models
        .iter()
        .filter(|m| {
            (m.provider == "vllm" || m.provider == "mlx") && m.assigned && m.port > 0
        })
        .cloned()
        .collect();

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

    // Read model_config.yml for purposes -- try remote first, then local
    let repo_root = app.repo_root.clone();
    let config_path = repo_root.join("provision/ansible/group_vars/all/model_config.yml");
    let config_contents = if is_remote {
        if let Some((ref host, ref user, ref key)) = ssh_details {
            let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
            let cmd = "cat ~/busibox/provision/ansible/group_vars/all/model_config.yml 2>/dev/null";
            ssh.run(cmd).unwrap_or_default()
        } else {
            std::fs::read_to_string(&config_path).unwrap_or_default()
        }
    } else {
        std::fs::read_to_string(&config_path).unwrap_or_default()
    };

    let purposes = benchmark::parse_model_purposes(&config_contents);
    let testable = benchmark::testable_chat_purposes(&purposes);

    let (tx, rx) = std::sync::mpsc::channel();
    app.benchmark_rx = Some(rx);

    std::thread::spawn(move || {
        let vllm_ip = if is_proxmox {
            format!("{vllm_network_base}.208")
        } else {
            "localhost".to_string()
        };

        // LiteLLM runs on .207 (agent-lxc container)
        let litellm_ip = if is_proxmox {
            format!("{vllm_network_base}.207")
        } else {
            "localhost".to_string()
        };
        let litellm_port: u16 = 4000;

        let prompt = "Say hello in one word.";
        let max_tokens: usize = 10;

        // --- Phase 1: Direct LLM tests (vLLM or MLX) ---
        let _ = tx.send(BenchmarkUpdate::Log(
            "=== Phase 1: Direct LLM Tests ===".into(),
        ));

        for model in &selected_models {
            let model_ip = if model.provider == "mlx" {
                "localhost".to_string()
            } else {
                vllm_ip.clone()
            };

            let test_name = format!("{}:{}", model.provider, model.api_model_name());
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                ">>> Testing {} [{}] (port {})...",
                model.api_model_name(),
                model.provider.to_uppercase(),
                model.port
            )));

            let curl_cmd = benchmark::build_curl_command(
                &model_ip,
                model.port,
                model.api_model_name(),
                prompt,
                max_tokens,
            );

            let result = match exec_curl(&curl_cmd, is_remote, &ssh_details) {
                Ok(output) => {
                    let mut r = benchmark::parse_model_test_response(&output);
                    r.test_name = test_name;
                    r.tier = ModelTestTier::DirectVllm;
                    r
                }
                Err(e) => benchmark::ModelTestResult {
                    test_name,
                    tier: ModelTestTier::DirectVllm,
                    passed: false,
                    response_content: None,
                    error: Some(e),
                    elapsed_ms: 0.0,
                },
            };

            let status = if result.passed { "PASS" } else { "FAIL" };
            let detail = result
                .response_content
                .as_deref()
                .or(result.error.as_deref())
                .unwrap_or("—");
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                "  {} [{status}] {:.0}ms - {}",
                result.test_name, result.elapsed_ms, detail
            )));

            let _ = tx.send(BenchmarkUpdate::ModelTestResult(result));
        }

        // --- Phase 2: LiteLLM proxy tests ---
        let _ = tx.send(BenchmarkUpdate::Log(String::new()));
        let _ = tx.send(BenchmarkUpdate::Log(
            "=== Phase 2: LiteLLM Proxy Tests ===".into(),
        ));

        // Read the litellm master key from the litellm container's config.
        // On Proxmox, /etc/default/litellm is on the litellm LXC (container 210),
        // not the host we SSH into. Use pct exec to read it.
        let litellm_key = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
                // Try reading from the litellm container via pct (Proxmox)
                let cmd = if is_proxmox {
                    "pct exec 207 -- sh -c 'cat /etc/default/litellm 2>/dev/null' 2>/dev/null | sed -n \"s/^LITELLM_MASTER_KEY=//p\" | head -1"
                } else {
                    "sed -n 's/^LITELLM_MASTER_KEY=//p' /etc/default/litellm 2>/dev/null | head -1"
                };
                ssh.run(cmd).unwrap_or_default().trim().to_string()
            } else {
                String::new()
            }
        } else {
            String::new()
        };

        if litellm_key.is_empty() && is_proxmox {
            let _ = tx.send(BenchmarkUpdate::Log(
                "  WARNING: Could not read LiteLLM master key; tests may fail with 401".into(),
            ));
        }

        for purpose in &testable {
            let test_name = format!("litellm:{purpose}");
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                ">>> Testing purpose '{purpose}' via LiteLLM..."
            )));

            let curl_cmd = benchmark::build_litellm_curl_command(
                &litellm_ip,
                litellm_port,
                purpose,
                &litellm_key,
                prompt,
                max_tokens,
            );

            let result = match exec_curl(&curl_cmd, is_remote, &ssh_details) {
                Ok(output) => {
                    let mut r = benchmark::parse_model_test_response(&output);
                    r.test_name = test_name;
                    r.tier = ModelTestTier::LiteLLM;
                    r
                }
                Err(e) => benchmark::ModelTestResult {
                    test_name,
                    tier: ModelTestTier::LiteLLM,
                    passed: false,
                    response_content: None,
                    error: Some(e),
                    elapsed_ms: 0.0,
                },
            };

            let status = if result.passed { "PASS" } else { "FAIL" };
            let detail = result
                .response_content
                .as_deref()
                .or(result.error.as_deref())
                .unwrap_or("—");
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                "  {} [{status}] {:.0}ms - {}",
                result.test_name, result.elapsed_ms, detail
            )));

            let _ = tx.send(BenchmarkUpdate::ModelTestResult(result));
        }

        // --- Phase 3: Service model tests (embedding, TTS, STT, image) ---
        let _ = tx.send(BenchmarkUpdate::Log(String::new()));
        let _ = tx.send(BenchmarkUpdate::Log(
            "=== Phase 3: Service Model Tests ===".into(),
        ));

        // Embedding service runs on data-lxc (:206) port 8005
        let embed_ip = if is_proxmox {
            format!("{vllm_network_base}.206")
        } else {
            "localhost".to_string()
        };
        let embed_port: u16 = 8005;

        let _ = tx.send(BenchmarkUpdate::Log(
            ">>> Testing embedding service...".into(),
        ));
        let embed_cmd = benchmark::build_embedding_curl_command(&embed_ip, embed_port);
        let embed_result = match exec_curl(&embed_cmd, is_remote, &ssh_details) {
            Ok(output) => {
                let mut r = benchmark::parse_embedding_response(&output);
                r.test_name = "embedding".to_string();
                r
            }
            Err(e) => benchmark::ModelTestResult {
                test_name: "embedding".to_string(),
                tier: benchmark::ModelTestTier::Service,
                passed: false,
                response_content: None,
                error: Some(e),
                elapsed_ms: 0.0,
            },
        };
        let status = if embed_result.passed { "PASS" } else { "FAIL" };
        let detail = embed_result
            .response_content
            .as_deref()
            .or(embed_result.error.as_deref())
            .unwrap_or("—");
        let _ = tx.send(BenchmarkUpdate::Log(format!(
            "  embedding [{status}] {:.0}ms - {detail}",
            embed_result.elapsed_ms
        )));
        let _ = tx.send(BenchmarkUpdate::ModelTestResult(embed_result));

        // TTS via LiteLLM
        if purposes.contains_key("voice") {
            let _ = tx.send(BenchmarkUpdate::Log(
                ">>> Testing TTS (voice) via LiteLLM...".into(),
            ));
            let tts_cmd = benchmark::build_tts_curl_command(&litellm_ip, litellm_port, &litellm_key);
            let tts_result = match exec_curl(&tts_cmd, is_remote, &ssh_details) {
                Ok(output) => {
                    let mut r = benchmark::parse_tts_response(&output);
                    r.test_name = "voice (TTS)".to_string();
                    r
                }
                Err(e) => benchmark::ModelTestResult {
                    test_name: "voice (TTS)".to_string(),
                    tier: benchmark::ModelTestTier::Service,
                    passed: false,
                    response_content: None,
                    error: Some(e),
                    elapsed_ms: 0.0,
                },
            };
            let status = if tts_result.passed { "PASS" } else { "FAIL" };
            let detail = tts_result
                .response_content
                .as_deref()
                .or(tts_result.error.as_deref())
                .unwrap_or("—");
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                "  voice [{status}] {:.0}ms - {detail}",
                tts_result.elapsed_ms
            )));
            let _ = tx.send(BenchmarkUpdate::ModelTestResult(tts_result));
        }

        // STT via LiteLLM
        if purposes.contains_key("transcribe") {
            let _ = tx.send(BenchmarkUpdate::Log(
                ">>> Testing STT (transcribe) via LiteLLM...".into(),
            ));
            let stt_cmd = benchmark::build_stt_curl_command(&litellm_ip, litellm_port, &litellm_key);
            let stt_result = match exec_curl(&stt_cmd, is_remote, &ssh_details) {
                Ok(output) => {
                    let mut r = benchmark::parse_stt_response(&output);
                    r.test_name = "transcribe (STT)".to_string();
                    r
                }
                Err(e) => benchmark::ModelTestResult {
                    test_name: "transcribe (STT)".to_string(),
                    tier: benchmark::ModelTestTier::Service,
                    passed: false,
                    response_content: None,
                    error: Some(e),
                    elapsed_ms: 0.0,
                },
            };
            let status = if stt_result.passed { "PASS" } else { "FAIL" };
            let detail = stt_result
                .response_content
                .as_deref()
                .or(stt_result.error.as_deref())
                .unwrap_or("—");
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                "  transcribe [{status}] {:.0}ms - {detail}",
                stt_result.elapsed_ms
            )));
            let _ = tx.send(BenchmarkUpdate::ModelTestResult(stt_result));
        }

        // Image generation via LiteLLM
        if purposes.contains_key("image") {
            let _ = tx.send(BenchmarkUpdate::Log(
                ">>> Testing image generation via LiteLLM...".into(),
            ));
            let img_cmd = benchmark::build_image_curl_command(&litellm_ip, litellm_port, &litellm_key);
            let img_result = match exec_curl(&img_cmd, is_remote, &ssh_details) {
                Ok(output) => {
                    let mut r = benchmark::parse_image_response(&output);
                    r.test_name = "image".to_string();
                    r
                }
                Err(e) => benchmark::ModelTestResult {
                    test_name: "image".to_string(),
                    tier: benchmark::ModelTestTier::Service,
                    passed: false,
                    response_content: None,
                    error: Some(e),
                    elapsed_ms: 0.0,
                },
            };
            let status = if img_result.passed { "PASS" } else { "FAIL" };
            let detail = img_result
                .response_content
                .as_deref()
                .or(img_result.error.as_deref())
                .unwrap_or("—");
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                "  image [{status}] {:.0}ms - {detail}",
                img_result.elapsed_ms
            )));
            let _ = tx.send(BenchmarkUpdate::ModelTestResult(img_result));
        }

        // Summary
        let _ = tx.send(BenchmarkUpdate::Log(String::new()));
        let _ = tx.send(BenchmarkUpdate::Log(
            "✓ Model tests complete".into(),
        ));
        let _ = tx.send(BenchmarkUpdate::Complete);
    });
}

struct AgentChatResult {
    elapsed_ms: f64,
    ttft_ms: f64,
    success: bool,
    error: Option<String>,
}

fn parse_agent_parallel_output(output: &str, count: usize) -> (Vec<AgentChatResult>, Option<f64>) {
    let mut results = Vec::new();
    for i in 0..count {
        let start_marker = format!("---BENCH_REQ:{i}---");
        let end_marker = format!("---BENCH_REQ_END:{i}---");
        let start_pos = match output.find(&start_marker) {
            Some(p) => p + start_marker.len(),
            None => continue,
        };
        let end_pos = output[start_pos..].find(&end_marker)
            .map(|p| start_pos + p)
            .unwrap_or(output.len());
        let block = &output[start_pos..end_pos];

        let mut elapsed_ms = 0.0_f64;
        let mut ttft_ms = 0.0_f64;
        let mut success = false;
        let mut error: Option<String> = None;

        if let Some(tp) = block.find("---BENCH_TIME:") {
            let after = &block[tp + "---BENCH_TIME:".len()..];
            if let Some(end) = after.find("---") {
                if let Ok(secs) = after[..end].trim().parse::<f64>() {
                    elapsed_ms = secs * 1000.0;
                }
            }
        }
        if let Some(tp) = block.find("---BENCH_TTFT:") {
            let after = &block[tp + "---BENCH_TTFT:".len()..];
            if let Some(end) = after.find("---") {
                if let Ok(secs) = after[..end].trim().parse::<f64>() {
                    ttft_ms = secs * 1000.0;
                }
            }
        }
        // Check response body for success/error
        let body = block.split("---BENCH_TIME:").next().unwrap_or(block).trim();
        if let Ok(json) = serde_json::from_str::<serde_json::Value>(body) {
            if json.get("message").is_some() || json.get("content").is_some() {
                success = true;
            } else if let Some(detail) = json.get("detail").and_then(|d| d.as_str()) {
                error = Some(detail.chars().take(60).collect());
            } else if let Some(msg) = json.get("error").and_then(|e| e.get("message")).and_then(|m| m.as_str()) {
                error = Some(msg.chars().take(60).collect());
            } else if elapsed_ms > 0.0 {
                success = true; // got a response with timing, assume ok
            }
        } else if elapsed_ms > 0.0 && !body.contains("error") {
            success = true;
        } else if !body.is_empty() {
            error = Some(body.chars().take(60).collect());
        }

        results.push(AgentChatResult { elapsed_ms, ttft_ms, success, error });
    }

    // Parse overall wall time
    let wall_secs = output.find("---BENCH_WALL:").and_then(|p| {
        let after = &output[p + "---BENCH_WALL:".len()..];
        after.find("---").and_then(|end| after[..end].trim().parse::<f64>().ok())
    }).map(|ns| ns / 1_000_000_000.0);

    (results, wall_secs)
}

fn start_load_test(app: &mut App) {
    match app.load_test_level {
        0 => start_engine_load_test(app),
        1 => start_litellm_load_test(app),
        _ => start_agent_load_test(app),
    }
}

// =============================================================================
// Shared concurrency runner
// =============================================================================

fn log_concurrency_results(
    tx: &std::sync::mpsc::Sender<BenchmarkUpdate>,
    concurrency: usize,
    results: Vec<AgentChatResult>,
    wall_secs: Option<f64>,
) {
    let successful = results.iter().filter(|r| r.success).count();
    let failed = concurrency - successful;
    let mut latencies: Vec<f64> = results.iter().filter(|r| r.success).map(|r| r.elapsed_ms).collect();
    let mut ttfts: Vec<f64> = results.iter().filter(|r| r.success && r.ttft_ms > 0.0).map(|r| r.ttft_ms).collect();
    let wall = wall_secs.unwrap_or_else(|| latencies.iter().cloned().fold(0.0_f64, f64::max) / 1000.0);

    if successful == 0 {
        let _ = tx.send(BenchmarkUpdate::Log(format!("  ERROR: 0/{concurrency} succeeded")));
        for r in &results {
            if let Some(ref e) = r.error {
                let _ = tx.send(BenchmarkUpdate::Log(format!("    {e}")));
            }
        }
    } else {
        let p50_lat = benchmark::median(&mut latencies);
        let p95_lat = percentile(&mut latencies.clone(), 95);
        let ttft_p50 = if ttfts.is_empty() { 0.0 } else { benchmark::median(&mut ttfts) };
        let ttft_p95 = if ttfts.is_empty() { 0.0 } else { percentile(&mut ttfts.clone(), 95) };
        let rps = if wall > 0.0 { successful as f64 / wall } else { 0.0 };
        let _ = tx.send(BenchmarkUpdate::Log(format!(
            "  ✓ {successful}/{concurrency} ok  |  wall: {:.1}s  |  rps: {:.2}", wall, rps
        )));
        let _ = tx.send(BenchmarkUpdate::Log(format!(
            "    Latency  p50: {:.0}ms  p95: {:.0}ms", p50_lat, p95_lat
        )));
        if ttft_p50 > 0.0 {
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                "    TTFT     p50: {:.0}ms  p95: {:.0}ms", ttft_p50, ttft_p95
            )));
        }
        if failed > 0 {
            let _ = tx.send(BenchmarkUpdate::Log(format!("    Failures: {failed}")));
        }
    }
    let _ = tx.send(BenchmarkUpdate::Log(String::new()));
}

fn run_openai_concurrency_levels(
    base_url: &str,
    model_name: &str,
    auth_header: &str,
    is_remote: bool,
    ssh_details: &Option<(String, String, String)>,
    tx: &std::sync::mpsc::Sender<BenchmarkUpdate>,
) {
    let prompt = "Reply in one sentence.";
    let timeout_secs: u64 = 90;
    let levels: &[usize] = &[1, 2, 4, 8];

    for &concurrency in levels {
        let _ = tx.send(BenchmarkUpdate::Log(format!("--- Concurrency: {concurrency} ---")));
        let body = format!(
            r#"{{"model":"{model_name}","messages":[{{"role":"user","content":"{prompt}"}}],"stream":false}}"#
        );
        let single = format!(
            r#"(S=$(date +%s%N 2>/dev/null||echo 0); curl -sS --max-time {timeout_secs} {auth_header} -H 'Content-Type: application/json' -d '{body}' '{base_url}/v1/chat/completions'; E=$(date +%s%N 2>/dev/null||echo 0); echo "---BENCH_TIME:$(echo "scale=6;($E-$S)/1000000000"|bc)---")"#
        );
        let cmd = build_parallel_shell_cmd(&single, concurrency);
        match exec_curl(&cmd, is_remote, ssh_details) {
            Ok(output) => {
                let (results, wall_secs) = parse_agent_parallel_output(&output, concurrency);
                log_concurrency_results(tx, concurrency, results, wall_secs);
            }
            Err(e) => {
                let _ = tx.send(BenchmarkUpdate::Log(format!("  ERROR: {e}")));
                let _ = tx.send(BenchmarkUpdate::Log(String::new()));
            }
        }
    }
}

fn build_parallel_shell_cmd(single_cmd: &str, count: usize) -> String {
    let mut s = String::new();
    s.push_str("_BD=$(mktemp -d); WALL_START=$(date +%s%N 2>/dev/null||echo 0); ");
    for i in 0..count {
        s.push_str(&format!("( {single_cmd} > \"$_BD/{i}.out\" 2>&1 ) & "));
    }
    s.push_str("wait; WALL_END=$(date +%s%N 2>/dev/null||echo 0); ");
    for i in 0..count {
        s.push_str(&format!(
            "echo '---BENCH_REQ:{i}---'; cat \"$_BD/{i}.out\"; echo ''; echo '---BENCH_REQ_END:{i}---'; "
        ));
    }
    s.push_str("echo \"---BENCH_WALL:$(( WALL_END - WALL_START ))---\"; rm -rf \"$_BD\"");
    s
}

// =============================================================================
// Level 0: Direct model engine (MLX / vLLM) — tests ALL deployed models
// =============================================================================

fn start_engine_load_test(app: &mut App) {
    app.benchmark_running = true;
    app.benchmark_complete = false;
    app.benchmark_log.clear();
    app.benchmark_log_scroll = 0;
    app.benchmark_tick = 0;

    let is_remote = app.active_profile().map(|(_, p)| p.remote).unwrap_or(false);
    let is_proxmox = app.active_profile().map(|(_, p)| p.backend == "proxmox").unwrap_or(false);
    let vllm_network_base: String = app
        .active_profile()
        .map(|(_, p)| p.vllm_network_base().to_string())
        .unwrap_or_else(|| "10.96.200".to_string());
    let ssh_details = get_ssh_details(app);

    let selected = match app.benchmark_models.get(app.load_test_model_idx) {
        Some(m) => m.clone(),
        None => {
            app.benchmark_log.push("ERROR: No deployed models found. Deploy a model first.".into());
            app.benchmark_running = false;
            app.benchmark_complete = true;
            return;
        }
    };

    let base_url = if is_proxmox {
        if selected.provider == "vllm" {
            format!("http://{vllm_network_base}.208:{}", selected.port)
        } else {
            format!("http://{vllm_network_base}.211:{}", selected.port)
        }
    } else {
        format!("http://localhost:{}", selected.port)
    };

    let provider_label = selected.provider.to_uppercase();
    let config_model_name = selected.model_name.clone();

    let (tx, rx) = std::sync::mpsc::channel();
    app.benchmark_rx = Some(rx);

    std::thread::spawn(move || {
        let _ = tx.send(BenchmarkUpdate::Log(format!(
            ">>> Engine: {provider_label} — {config_model_name} @ {base_url}"
        )));
        let _ = tx.send(BenchmarkUpdate::Log(
            ">>> Querying /v1/models for actual served model name...".into()
        ));

        let models_url = format!("{base_url}/v1/models");
        let curl_cmd = format!("curl -s --max-time 5 '{models_url}'");
        let raw = exec_curl(&curl_cmd, is_remote, &ssh_details).unwrap_or_default();
        let served_models: Vec<String> = serde_json::from_str::<serde_json::Value>(&raw)
            .ok()
            .and_then(|v| v["data"].as_array().cloned())
            .unwrap_or_default()
            .iter()
            .filter_map(|m| m["id"].as_str().map(|s| s.to_string()))
            .collect();

        if served_models.is_empty() {
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                "ERROR: /v1/models at {base_url} returned no models (engine may be down)"
            )));
            let _ = tx.send(BenchmarkUpdate::Complete);
            return;
        }

        let model_name = &served_models[0];
        let _ = tx.send(BenchmarkUpdate::Log(format!(
            ">>> Served model: {model_name}"
        )));
        let _ = tx.send(BenchmarkUpdate::Log(String::new()));

        run_openai_concurrency_levels(
            &base_url,
            model_name,
            "",
            is_remote,
            &ssh_details,
            &tx,
        );

        let _ = tx.send(BenchmarkUpdate::Log("✓ Engine load test complete".into()));
        let _ = tx.send(BenchmarkUpdate::Complete);
    });
}

// =============================================================================
// Level 1: LiteLLM proxy
// =============================================================================

fn start_litellm_load_test(app: &mut App) {
    app.benchmark_running = true;
    app.benchmark_complete = false;
    app.benchmark_log.clear();
    app.benchmark_log_scroll = 0;
    app.benchmark_tick = 0;

    let is_remote = app.active_profile().map(|(_, p)| p.remote).unwrap_or(false);
    let is_proxmox = app.active_profile().map(|(_, p)| p.backend == "proxmox").unwrap_or(false);
    let vllm_network_base: String = app
        .active_profile()
        .map(|(_, p)| p.vllm_network_base().to_string())
        .unwrap_or_else(|| "10.96.200".to_string());
    let ssh_details = get_ssh_details(app);

    let vault_prefix: String = app
        .active_profile()
        .and_then(|(id, p)| p.vault_prefix.clone().or(Some(id.to_string())))
        .unwrap_or_else(|| "dev".into());
    let vault_password = app.vault_password.clone();
    let repo_root = app.repo_root.clone();
    let remote_path: String = app
        .active_profile()
        .map(|(_, p)| p.effective_remote_path().to_string())
        .unwrap_or_else(|| "~/busibox".to_string());

    let model_name = app
        .benchmark_models
        .first()
        .map(|m| m.model_key.clone())
        .unwrap_or_else(|| "agent-model".to_string());

    let base_url = if is_proxmox {
        format!("http://{vllm_network_base}.207:4000")
    } else {
        "http://localhost:4000".to_string()
    };

    let (tx, rx) = std::sync::mpsc::channel();
    app.benchmark_rx = Some(rx);

    std::thread::spawn(move || {
        let vault_password = match vault_password {
            None => {
                let _ = tx.send(BenchmarkUpdate::Log(
                    "ERROR: Vault not unlocked — restart the CLI to unlock vault.".into(),
                ));
                let _ = tx.send(BenchmarkUpdate::Complete);
                return;
            }
            Some(p) => p,
        };

        let vault_file_rel = format!("provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml");
        let py = build_vault_read_script("litellm_api_key");

        let api_key = match run_vault_read(
            &py, &vault_password, &vault_file_rel, &repo_root,
            is_remote, &ssh_details, &remote_path, &tx,
        ) {
            Ok(v) => v,
            Err(_) => {
                let _ = tx.send(BenchmarkUpdate::Complete);
                return;
            }
        };

        if api_key.is_empty() {
            let _ = tx.send(BenchmarkUpdate::Log(
                "WARN: litellm_api_key not set in vault — proceeding without auth".into(),
            ));
        } else {
            let _ = tx.send(BenchmarkUpdate::Log(">>> litellm_api_key loaded from vault".into()));
        }

        let auth_header = if api_key.is_empty() {
            String::new()
        } else {
            format!("-H 'Authorization: Bearer {api_key}'")
        };

        let _ = tx.send(BenchmarkUpdate::Log(format!(">>> LiteLLM: {base_url}")));
        let _ = tx.send(BenchmarkUpdate::Log(format!(">>> Model:   {model_name}")));
        let _ = tx.send(BenchmarkUpdate::Log(String::new()));

        run_openai_concurrency_levels(&base_url, &model_name, &auth_header, is_remote, &ssh_details, &tx);
        let _ = tx.send(BenchmarkUpdate::Log("✓ LiteLLM load test complete".into()));
        let _ = tx.send(BenchmarkUpdate::Complete);
    });
}

// =============================================================================
// Level 2: Agent-API (auto-fetch JWT from vault delegation token)
// =============================================================================

fn start_agent_load_test(app: &mut App) {
    app.benchmark_running = true;
    app.benchmark_complete = false;
    app.benchmark_log.clear();
    app.benchmark_log_scroll = 0;
    app.benchmark_tick = 0;

    let is_remote = app.active_profile().map(|(_, p)| p.remote).unwrap_or(false);
    let is_proxmox = app.active_profile().map(|(_, p)| p.backend == "proxmox").unwrap_or(false);
    let network_base: String = app
        .active_profile()
        .map(|(_, p)| p.effective_network_base().to_string())
        .unwrap_or_else(|| "10.96.200".to_string());
    let ssh_details = get_ssh_details(app);

    let vault_prefix: String = app
        .active_profile()
        .and_then(|(id, p)| p.vault_prefix.clone().or(Some(id.to_string())))
        .unwrap_or_else(|| "dev".into());
    let vault_password = app.vault_password.clone();
    let repo_root = app.repo_root.clone();
    let remote_path: String = app
        .active_profile()
        .map(|(_, p)| p.effective_remote_path().to_string())
        .unwrap_or_else(|| "~/busibox".to_string());

    let agent_api_url = if is_proxmox {
        format!("http://{network_base}.202:8000")
    } else {
        "http://localhost:8000".to_string()
    };
    let authz_ip = if is_proxmox {
        format!("{network_base}.210")
    } else {
        "localhost".to_string()
    };

    let (tx, rx) = std::sync::mpsc::channel();
    app.benchmark_rx = Some(rx);

    std::thread::spawn(move || {
        let vault_password = match vault_password {
            None => {
                let _ = tx.send(BenchmarkUpdate::Log(
                    "ERROR: Vault not unlocked — restart the CLI to unlock vault.".into(),
                ));
                let _ = tx.send(BenchmarkUpdate::Complete);
                return;
            }
            Some(p) => p,
        };

        let vault_file_rel = format!("provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml");
        let py = build_vault_read_script("bridge.delegation_token");

        let delegation_token = match run_vault_read(
            &py, &vault_password, &vault_file_rel, &repo_root,
            is_remote, &ssh_details, &remote_path, &tx,
        ) {
            Ok(v) => v,
            Err(_) => {
                let _ = tx.send(BenchmarkUpdate::Complete);
                return;
            }
        };

        if delegation_token.is_empty() {
            let _ = tx.send(BenchmarkUpdate::Log(
                "ERROR: No delegation token in vault.".into(),
            ));
            let _ = tx.send(BenchmarkUpdate::Log(
                "  Create one: POST /oauth/delegation with an admin session JWT".into(),
            ));
            let _ = tx.send(BenchmarkUpdate::Log(
                "  then store it at secrets.bridge.delegation_token in the vault.".into(),
            ));
            let _ = tx.send(BenchmarkUpdate::Complete);
            return;
        }

        let _ = tx.send(BenchmarkUpdate::Log(
            ">>> delegation_token loaded, exchanging for agent-api JWT...".into(),
        ));

        // Exchange delegation token → short-lived JWT via authz
        let exchange_py = build_token_exchange_script(&authz_ip, &delegation_token);
        let exchange_cmd = format!("python3 -c {}", shell_escape_str(&exchange_py));
        let jwt = match exec_curl(&exchange_cmd, is_remote, &ssh_details) {
            Ok(out) => {
                let t = out.trim().to_string();
                if t.is_empty() || t.starts_with("ERROR") {
                    let _ = tx.send(BenchmarkUpdate::Log(format!(
                        "ERROR: token exchange failed: {t}"
                    )));
                    let _ = tx.send(BenchmarkUpdate::Complete);
                    return;
                }
                t
            }
            Err(e) => {
                let _ = tx.send(BenchmarkUpdate::Log(format!(
                    "ERROR: token exchange request failed: {e}"
                )));
                let _ = tx.send(BenchmarkUpdate::Complete);
                return;
            }
        };

        let _ = tx.send(BenchmarkUpdate::Log(">>> agent-api JWT obtained".into()));
        let _ = tx.send(BenchmarkUpdate::Log(format!(">>> Agent API: {agent_api_url}")));
        let _ = tx.send(BenchmarkUpdate::Log(
            ">>> Concurrency levels: 1, 2, 4, 8".into(),
        ));
        let _ = tx.send(BenchmarkUpdate::Log(String::new()));

        let prompt = "Reply in one sentence.";
        let timeout_secs: u64 = 120;
        let levels: &[usize] = &[1, 2, 4, 8];

        for &concurrency in levels {
            let _ = tx.send(BenchmarkUpdate::Log(format!("--- Concurrency: {concurrency} ---")));
            let cmd = benchmark::build_parallel_agent_chat_command(
                &agent_api_url,
                &jwt,
                prompt,
                concurrency,
                timeout_secs,
            );
            match exec_curl(&cmd, is_remote, &ssh_details) {
                Ok(output) => {
                    let (results, wall_secs) = parse_agent_parallel_output(&output, concurrency);
                    log_concurrency_results(&tx, concurrency, results, wall_secs);
                }
                Err(e) => {
                    let _ = tx.send(BenchmarkUpdate::Log(format!("  ERROR: {e}")));
                    let _ = tx.send(BenchmarkUpdate::Log(String::new()));
                }
            }
        }

        let _ = tx.send(BenchmarkUpdate::Log("✓ Agent-API load test complete".into()));
        let _ = tx.send(BenchmarkUpdate::Complete);
    });
}

// =============================================================================
// Load test helpers
// =============================================================================

fn get_ssh_details(app: &App) -> Option<(String, String, String)> {
    if app.active_profile().map(|(_, p)| p.remote).unwrap_or(false) {
        app.active_profile().and_then(|(_, p)| {
            p.effective_host().map(|h| {
                (h.to_string(), p.effective_user().to_string(), p.effective_ssh_key().to_string())
            })
        })
    } else {
        None
    }
}

/// Build a Python script that decrypts the vault and prints the value at `dotted.key.path`
/// under the top-level `secrets` map.  The vault file path is injected at runtime by the caller
/// via the ANSIBLE_VAULT_FILEPATH env var, so the script itself is path-agnostic.
fn build_vault_read_script(dotted_key: &str) -> String {
    let key_escaped = dotted_key.replace('\'', "\\'");
    format!(
        r#"
import subprocess, yaml, os, sys, tempfile
vault_file = os.environ.get('ANSIBLE_VAULT_FILEPATH', '')
vault_pass = os.environ.get('ANSIBLE_VAULT_PASSWORD', '')
if not vault_pass:
    print('ERROR: ANSIBLE_VAULT_PASSWORD not set'); sys.exit(1)
if not vault_file:
    print('ERROR: ANSIBLE_VAULT_FILEPATH not set'); sys.exit(1)
with tempfile.NamedTemporaryFile(mode='w', suffix='.tmp', delete=False) as f:
    f.write(vault_pass); tmp = f.name
try:
    r = subprocess.run(
        ['ansible-vault', 'decrypt', '--output', '-', '--vault-password-file', tmp, vault_file],
        capture_output=True, text=True)
    if r.returncode != 0:
        print('ERROR: decrypt failed: ' + r.stderr.strip()); sys.exit(1)
    data = yaml.safe_load(r.stdout) or {{}}
    val = data.get('secrets', {{}})
    for part in '{key_escaped}'.split('.'):
        val = val.get(part, '') if isinstance(val, dict) else ''
    print(val or '')
finally:
    os.unlink(tmp)
"#,
        key_escaped = key_escaped,
    )
}

/// Run a vault-read python script, setting ANSIBLE_VAULT_FILEPATH correctly for both
/// local (absolute path) and remote (relative, run inside remote_path) cases.
/// Returns the trimmed output string, or an Err after logging to `tx`.
fn run_vault_read(
    py_script: &str,
    vault_password: &str,
    vault_file_rel: &str,
    repo_root: &std::path::Path,
    is_remote: bool,
    ssh_details: &Option<(String, String, String)>,
    remote_path: &str,
    tx: &std::sync::mpsc::Sender<BenchmarkUpdate>,
) -> Result<String, ()> {
    if is_remote {
        let Some((ref host, ref user, ref key)) = ssh_details else {
            let _ = tx.send(BenchmarkUpdate::Log("ERROR: No SSH credentials".into()));
            return Err(());
        };
        let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
        // Expand ~/... to $HOME/... so double-quoting lets the shell expand $HOME.
        // shell_escape_str uses single quotes which prevent tilde expansion.
        let path_clean = remote_path.trim_end_matches('/');
        let cd_path = if path_clean.starts_with("~/") {
            format!("\"$HOME{}\"", &path_clean[1..])
        } else {
            shell_escape_str(path_clean)
        };
        let cmd = format!(
            "cd {} && ANSIBLE_VAULT_PASSWORD={} ANSIBLE_VAULT_FILEPATH={} python3 -c {}",
            cd_path,
            shell_escape_str(vault_password),
            shell_escape_str(vault_file_rel),
            shell_escape_str(py_script),
        );
        let full_cmd = format!("{}{}", crate::modules::remote::SHELL_PATH_PREAMBLE, cmd);
        match ssh.run(&full_cmd).map_err(|e| format!("SSH exec failed: {e}")) {
            Ok(out) => {
                let v = out.trim().to_string();
                if v.starts_with("ERROR") {
                    let _ = tx.send(BenchmarkUpdate::Log(format!("ERROR: vault read: {v}")));
                    return Err(());
                }
                Ok(v)
            }
            Err(e) => {
                let _ = tx.send(BenchmarkUpdate::Log(format!("ERROR: vault read failed: {e}")));
                Err(())
            }
        }
    } else {
        let vault_abs = repo_root.join(vault_file_rel);
        if !vault_abs.exists() {
            let _ = tx.send(BenchmarkUpdate::Log(format!(
                "ERROR: vault file not found: {}", vault_abs.display()
            )));
            return Err(());
        }
        let out = std::process::Command::new("python3")
            .arg("-c")
            .arg(py_script)
            .env("ANSIBLE_VAULT_PASSWORD", vault_password)
            .env("ANSIBLE_VAULT_FILEPATH", vault_abs.to_string_lossy().as_ref())
            .output()
            .map_err(|e| format!("exec failed: {e}"));
        match out {
            Ok(o) => {
                let v = String::from_utf8_lossy(&o.stdout).trim().to_string();
                if v.starts_with("ERROR") {
                    let _ = tx.send(BenchmarkUpdate::Log(format!("ERROR: vault read: {v}")));
                    return Err(());
                }
                Ok(v)
            }
            Err(e) => {
                let _ = tx.send(BenchmarkUpdate::Log(format!("ERROR: vault python failed: {e}")));
                Err(())
            }
        }
    }
}

/// Python script to exchange a delegation token for an agent-api JWT via authz.
fn build_token_exchange_script(authz_ip: &str, delegation_token: &str) -> String {
    let authz_escaped = authz_ip.replace('\'', "\\'");
    let token_escaped = delegation_token.replace('\'', "\\'");
    format!(
        r#"
import urllib.request, json, sys
payload = json.dumps({{
    'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
    'subject_token': '{token_escaped}',
    'audience': 'agent-api'
}}).encode()
req = urllib.request.Request(
    'http://{authz_escaped}:8010/oauth/token',
    data=payload, method='POST',
    headers={{'Content-Type': 'application/json'}}
)
try:
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read())
    print(data.get('access_token', ''))
except Exception as e:
    print('ERROR: ' + str(e)); sys.exit(1)
"#,
        authz_escaped = authz_escaped,
        token_escaped = token_escaped,
    )
}

// =============================================================================
// Cloud Keys Tab
// =============================================================================

const CLOUD_KEY_FIELDS: &[&str] = &[
    "OpenAI API Key",
    "Bedrock Access Key ID",
    "Bedrock Secret Access Key",
    "Bedrock Region",
];

fn render_cloud_keys(f: &mut Frame, app: &App, area: Rect) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),  // title
            Constraint::Length(10), // fields
            Constraint::Min(4),     // log
            Constraint::Length(2),  // help bar
        ])
        .split(area);

    let profile_name = app
        .active_profile()
        .map(|(id, _)| id.to_string())
        .unwrap_or_else(|| "no profile".into());

    let title = Paragraph::new(Line::from(vec![
        Span::styled("  Cloud API Keys", theme::highlight()),
        Span::styled(format!("  — profile: {profile_name}"), theme::dim()),
    ]))
    .block(Block::default().borders(Borders::BOTTOM).border_style(theme::dim()));
    f.render_widget(title, chunks[0]);

    // Fields
    let field_values = [
        app.cloud_keys_openai.as_str(),
        app.cloud_keys_bedrock_access.as_str(),
        app.cloud_keys_bedrock_secret.as_str(),
        app.cloud_keys_bedrock_region.as_str(),
    ];

    let mut field_lines: Vec<Line> = Vec::new();
    for (i, (label, value)) in CLOUD_KEY_FIELDS.iter().zip(field_values.iter()).enumerate() {
        let is_focused = app.cloud_keys_field == i;
        let is_editing = is_focused && app.cloud_keys_editing;

        let label_style = if is_focused { theme::highlight() } else { theme::normal() };
        let cursor = if is_editing { "█" } else { "" };

        let display = if i == 2 && !value.is_empty() && !is_editing {
            "*".repeat(value.len().min(20))
        } else {
            value.to_string()
        };

        let prefix = if is_focused { "▶ " } else { "  " };
        let value_style = if is_editing {
            theme::success()
        } else if value.is_empty() {
            theme::dim()
        } else {
            theme::normal()
        };

        field_lines.push(Line::from(vec![
            Span::styled(format!("{prefix}{:<28}", label), label_style),
            Span::styled(
                if display.is_empty() { "<empty>".into() } else { display },
                value_style,
            ),
            Span::styled(cursor, theme::success()),
        ]));
    }

    let save_hint = if app.cloud_keys_saving {
        Line::from(Span::styled("  Saving…", theme::info()))
    } else if app.cloud_keys_save_complete {
        Line::from(Span::styled("  ✓ Saved — LiteLLM redeploying", theme::success()))
    } else {
        Line::from(vec![
            Span::styled("  [Enter] edit field   ", theme::dim()),
            Span::styled("[s] Save & Redeploy LiteLLM", theme::highlight()),
        ])
    };
    field_lines.push(Line::default());
    field_lines.push(save_hint);

    let fields_block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(Span::styled(" Provider Keys ", theme::dim()));
    let fields_para = Paragraph::new(field_lines)
        .block(fields_block)
        .wrap(Wrap { trim: false });
    f.render_widget(fields_para, chunks[1]);

    // Log
    let log_lines: Vec<Line> = app
        .cloud_keys_log
        .iter()
        .map(|s| {
            let style = if s.starts_with("ERROR") || s.starts_with("✗") {
                theme::error()
            } else if s.starts_with("✓") || s.starts_with("OK") {
                theme::success()
            } else if s.starts_with("---") || s.starts_with(">>>") {
                theme::info()
            } else {
                theme::normal()
            };
            Line::from(Span::styled(s.clone(), style))
        })
        .collect();

    let log_block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(Span::styled(" Save Log ", theme::dim()));
    let log_height = chunks[2].height.saturating_sub(2) as usize;
    let log_start = if app.cloud_keys_log.len() > log_height {
        app.cloud_keys_log.len() - log_height
    } else {
        0
    };
    let visible_lines: Vec<Line> = log_lines
        .into_iter()
        .skip(app.cloud_keys_log_scroll.min(log_start))
        .take(log_height + 1)
        .collect();
    let log_para = Paragraph::new(visible_lines).block(log_block);
    f.render_widget(log_para, chunks[2]);

    // Help bar
    let help = Paragraph::new(Line::from(vec![
        Span::styled("[↑↓] Navigate   ", theme::dim()),
        Span::styled("[Enter] Edit/Confirm   ", theme::dim()),
        Span::styled("[Backspace] Delete char   ", theme::dim()),
        Span::styled("[s] Save & Redeploy   ", theme::dim()),
        Span::styled("[Tab] Switch tab   ", theme::dim()),
        Span::styled("[Esc] Back", theme::dim()),
    ]));
    f.render_widget(help, chunks[3]);
}

fn save_cloud_keys(app: &mut App) {
    if app.cloud_keys_saving {
        return;
    }

    let vault_prefix: String = app
        .active_profile()
        .and_then(|(id, p)| p.vault_prefix.clone().or(Some(id.to_string())))
        .unwrap_or_else(|| "dev".into());

    let vault_password = match &app.vault_password {
        Some(p) => p.clone(),
        None => {
            app.cloud_keys_log.clear();
            app.cloud_keys_log.push(
                "ERROR: Vault not unlocked — restart busibox CLI to unlock vault first".into(),
            );
            return;
        }
    };

    let repo_root = app.repo_root.clone();
    let openai_key = app.cloud_keys_openai.clone();
    let bedrock_access = app.cloud_keys_bedrock_access.clone();
    let bedrock_secret = app.cloud_keys_bedrock_secret.clone();
    let bedrock_region = app.cloud_keys_bedrock_region.clone();

    let is_remote = app
        .active_profile()
        .map(|(_, p)| p.remote)
        .unwrap_or(false);
    let ssh_details: Option<(String, String, String)> = if is_remote {
        app.active_profile().and_then(|(_, p)| {
            p.effective_host().map(|h| {
                (
                    h.to_string(),
                    p.effective_user().to_string(),
                    p.effective_ssh_key().to_string(),
                )
            })
        })
    } else {
        None
    };
    let remote_path: String = app
        .active_profile()
        .map(|(_, p)| p.effective_remote_path().to_string())
        .unwrap_or_else(|| "~/busibox".to_string());

    let profile_environment: String = app
        .active_profile()
        .map(|(_, p)| p.environment.clone())
        .unwrap_or_else(|| "development".into());
    let container_prefix: String = app
        .active_profile()
        .map(|(_, p)| super::install::env_to_prefix(&p.environment))
        .unwrap_or_else(|| "dev".into());
    let profile_backend: String = app
        .active_profile()
        .map(|(_, p)| p.backend.clone())
        .unwrap_or_else(|| "docker".into());

    app.cloud_keys_saving = true;
    app.cloud_keys_save_complete = false;
    app.cloud_keys_log.clear();
    app.cloud_keys_log_scroll = 0;

    let (tx, rx) = std::sync::mpsc::channel::<CloudKeysUpdate>();
    app.cloud_keys_rx = Some(rx);

    std::thread::spawn(move || {
        let _ = tx.send(CloudKeysUpdate::Log(">>> Updating vault with API keys...".into()));

        let vault_file = format!(
            "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
        );

        // Build Python script to update vault
        // The bedrock key format expected by litellm.env.j2 is "ACCESS:SECRET"
        let bedrock_combined = if !bedrock_access.is_empty() && !bedrock_secret.is_empty() {
            format!("{}:{}", bedrock_access, bedrock_secret)
        } else {
            String::new()
        };

        let py_script = build_vault_update_script(
            &vault_file,
            &openai_key,
            &bedrock_combined,
            &bedrock_region,
        );

        let cmd = format!(
            "ANSIBLE_VAULT_PASSWORD={} python3 -c {}",
            shell_escape_str(&vault_password),
            shell_escape_str(&py_script),
        );

        let update_ok = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
                if let Err(e) = crate::modules::remote::sync(&repo_root, host, user, key, &remote_path) {
                    let _ = tx.send(CloudKeysUpdate::Log(format!("ERROR: rsync failed: {e}")));
                    let _ = tx.send(CloudKeysUpdate::Complete { success: false });
                    return;
                }
                let tx2 = tx.clone();
                let full_cmd = format!(
                    "cd {} && {}",
                    remote_path.trim_end_matches('/'),
                    cmd
                );
                let full_cmd_with_path = format!(
                    "{}{}",
                    crate::modules::remote::SHELL_PATH_PREAMBLE,
                    full_cmd
                );
                match ssh.run(&full_cmd_with_path) {
                    Ok(output) => {
                        for line in output.lines() {
                            let _ = tx2.send(CloudKeysUpdate::Log(format!("  {line}")));
                        }
                        !output.contains("ERROR")
                    }
                    Err(e) => {
                        let _ = tx.send(CloudKeysUpdate::Log(format!("ERROR: {e}")));
                        false
                    }
                }
            } else {
                let _ = tx.send(CloudKeysUpdate::Log("ERROR: No SSH credentials".into()));
                let _ = tx.send(CloudKeysUpdate::Complete { success: false });
                return;
            }
        } else {
            let vault_path = repo_root.join(&vault_file);
            if !vault_path.exists() {
                let _ = tx.send(CloudKeysUpdate::Log(format!(
                    "ERROR: Vault file not found: {}", vault_path.display()
                )));
                let _ = tx.send(CloudKeysUpdate::Complete { success: false });
                return;
            }

            let local_py_script = build_vault_update_script(
                &vault_path.to_string_lossy(),
                &openai_key,
                &bedrock_combined,
                &bedrock_region,
            );

            let output = std::process::Command::new("python3")
                .arg("-c")
                .arg(&local_py_script)
                .env("ANSIBLE_VAULT_PASSWORD", &vault_password)
                .output();

            match output {
                Ok(out) => {
                    let stdout = String::from_utf8_lossy(&out.stdout);
                    let stderr = String::from_utf8_lossy(&out.stderr);
                    for line in stdout.lines() {
                        let _ = tx.send(CloudKeysUpdate::Log(format!("  {line}")));
                    }
                    if !stderr.trim().is_empty() {
                        for line in stderr.lines() {
                            let _ = tx.send(CloudKeysUpdate::Log(format!("  STDERR: {line}")));
                        }
                    }
                    out.status.success() && !stdout.contains("ERROR")
                }
                Err(e) => {
                    let _ = tx.send(CloudKeysUpdate::Log(format!("ERROR: python3 exec failed: {e}")));
                    false
                }
            }
        };

        if !update_ok {
            let _ = tx.send(CloudKeysUpdate::Log("✗ Vault update failed".into()));
            let _ = tx.send(CloudKeysUpdate::Complete { success: false });
            return;
        }

        let _ = tx.send(CloudKeysUpdate::Log("✓ Vault updated".into()));

        // Redeploy LiteLLM
        let _ = tx.send(CloudKeysUpdate::Log(">>> Redeploying LiteLLM service...".into()));

        let env_prefix = format!(
            "ENV={profile_environment} \
             BUSIBOX_ENV={profile_environment} \
             BUSIBOX_BACKEND={profile_backend} \
             CONTAINER_PREFIX={container_prefix} \
             VAULT_PREFIX={vault_prefix} \
             ANSIBLE_VAULT_PASSWORD={} ",
            shell_escape_str(&vault_password),
        );

        let make_cmd = format!("{env_prefix} make install SERVICE=litellm");

        let deploy_ok = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
                let full = format!(
                    "{}cd {} && {}",
                    crate::modules::remote::SHELL_PATH_PREAMBLE,
                    remote_path.trim_end_matches('/'),
                    make_cmd
                );
                match ssh.run(&full) {
                    Ok(output) => {
                        for line in output.lines() {
                            let _ = tx.send(CloudKeysUpdate::Log(format!("  {line}")));
                        }
                        !output.to_lowercase().contains("error") || output.contains("PLAY RECAP")
                    }
                    Err(e) => {
                        let _ = tx.send(CloudKeysUpdate::Log(format!("ERROR: {e}")));
                        false
                    }
                }
            } else {
                false
            }
        } else {
            let result = std::process::Command::new("sh")
                .arg("-c")
                .arg(&make_cmd)
                .current_dir(&repo_root)
                .env("ANSIBLE_VAULT_PASSWORD", &vault_password)
                .output();
            match result {
                Ok(out) => {
                    let stdout = String::from_utf8_lossy(&out.stdout);
                    let stderr = String::from_utf8_lossy(&out.stderr);
                    for line in stdout.lines().take(40) {
                        let _ = tx.send(CloudKeysUpdate::Log(format!("  {line}")));
                    }
                    if !stderr.trim().is_empty() {
                        for line in stderr.lines().take(10) {
                            let _ = tx.send(CloudKeysUpdate::Log(format!("  {line}")));
                        }
                    }
                    out.status.success()
                }
                Err(e) => {
                    let _ = tx.send(CloudKeysUpdate::Log(format!("ERROR: make failed: {e}")));
                    false
                }
            }
        };

        if deploy_ok {
            let _ = tx.send(CloudKeysUpdate::Log("✓ LiteLLM redeployed with new credentials".into()));
            let _ = tx.send(CloudKeysUpdate::Complete { success: true });
        } else {
            let _ = tx.send(CloudKeysUpdate::Log("✗ LiteLLM redeploy failed — check logs".into()));
            let _ = tx.send(CloudKeysUpdate::Complete { success: false });
        }
    });
}

/// Build the Python inline script that decrypts the vault, updates keys, and re-encrypts.
fn build_vault_update_script(
    vault_file: &str,
    openai_key: &str,
    bedrock_combined: &str,
    bedrock_region: &str,
) -> String {
    let openai_escaped = openai_key.replace('\\', "\\\\").replace('\'', "\\'");
    let bedrock_escaped = bedrock_combined.replace('\\', "\\\\").replace('\'', "\\'");
    let region_escaped = bedrock_region.replace('\\', "\\\\").replace('\'', "\\'");
    let vault_escaped = vault_file.replace('\\', "\\\\").replace('\'', "\\'");

    format!(
        r#"
import subprocess, yaml, os, sys, tempfile

vault_file = '{vault_escaped}'
vault_pass = os.environ.get('ANSIBLE_VAULT_PASSWORD', '')
if not vault_pass:
    print('ERROR: ANSIBLE_VAULT_PASSWORD not set')
    sys.exit(1)

with tempfile.NamedTemporaryFile(mode='w', suffix='.tmp', delete=False) as f:
    f.write(vault_pass)
    tmp_pass = f.name

try:
    r = subprocess.run(
        ['ansible-vault', 'decrypt', '--vault-password-file', tmp_pass, vault_file],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        print('ERROR: decrypt failed: ' + r.stderr.strip())
        sys.exit(1)

    with open(vault_file) as f:
        data = yaml.safe_load(f) or {{}}

    secrets = data.setdefault('secrets', {{}})
    openai_key = '{openai_escaped}'
    bedrock_key = '{bedrock_escaped}'
    region = '{region_escaped}'

    if openai_key:
        secrets.setdefault('openai', {{}})['api_key'] = openai_key
        print('OK: OpenAI key updated')
    if bedrock_key:
        secrets.setdefault('bedrock', {{}})['api_key'] = bedrock_key
        print('OK: Bedrock key updated')
    if region:
        secrets.setdefault('bedrock', {{}})['region'] = region
        print('OK: Bedrock region updated')

    with open(vault_file, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    r = subprocess.run(
        ['ansible-vault', 'encrypt', '--vault-password-file', tmp_pass, vault_file],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        print('ERROR: encrypt failed: ' + r.stderr.strip())
        sys.exit(1)
    print('OK: Vault re-encrypted')
finally:
    os.unlink(tmp_pass)
"#,
        vault_escaped = vault_escaped,
        openai_escaped = openai_escaped,
        bedrock_escaped = bedrock_escaped,
        region_escaped = region_escaped,
    )
}

fn shell_escape_str(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn percentile(sorted: &mut Vec<f64>, p: usize) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let idx = ((p as f64 / 100.0) * (sorted.len() as f64 - 1.0)).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
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
