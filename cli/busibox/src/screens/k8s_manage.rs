use crate::app::{App, K8sManageUpdate, Screen};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};
use std::sync::mpsc;

struct Section {
    name: &'static str,
    actions: &'static [(&'static str, &'static str)],
}

const SECTIONS: &[Section] = &[
    Section {
        name: "Deployment",
        actions: &[
            ("Deploy All", "k8s-deploy"),
            ("Sync Code", "k8s-sync"),
            ("Build Images", "k8s-build"),
            ("Apply Manifests", "k8s-apply"),
            ("Update Secrets", "k8s-secrets"),
            ("Status", "k8s-status"),
            ("Logs", "k8s-logs"),
            ("Delete", "k8s-delete"),
        ],
    },
    Section {
        name: "Connectivity",
        actions: &[
            ("Connect", "connect"),
            ("Disconnect", "disconnect"),
            ("Connection Status", "k8s-connect-status"),
        ],
    },
    Section {
        name: "Spot Management",
        actions: &[
            ("Check Prices", "spot-check"),
            ("Swap Node Class", "spot-swap"),
            ("Change Bid", "spot-price"),
        ],
    },
    Section {
        name: "GPU Burst",
        actions: &[
            ("GPU Up", "k8s-gpu-up"),
            ("GPU Down", "k8s-gpu-down"),
            ("GPU Status", "k8s-gpu-status"),
            ("GPU Window", "k8s-gpu-window"),
        ],
    },
];

fn flat_actions() -> Vec<(usize, usize, &'static str, &'static str)> {
    let mut result = Vec::new();
    for (si, section) in SECTIONS.iter().enumerate() {
        for (ai, &(label, target)) in section.actions.iter().enumerate() {
            result.push((si, ai, label, target));
        }
    }
    result
}

pub fn render(f: &mut Frame, app: &App) {
    if app.k8s_manage_log_visible {
        render_log_viewer(f, app);
        return;
    }

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("K8s Management")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let actions = flat_actions();
    let mut rows: Vec<ListItem> = Vec::new();
    let mut current_section: Option<usize> = None;

    for (idx, &(si, _ai, label, _target)) in actions.iter().enumerate() {
        if current_section != Some(si) {
            current_section = Some(si);
            if si > 0 {
                rows.push(ListItem::new(Line::from("")));
            }
            rows.push(ListItem::new(Line::from(Span::styled(
                format!("  {}", SECTIONS[si].name),
                theme::heading(),
            ))));
        }

        let style = if idx == app.k8s_manage_selected {
            theme::selected()
        } else {
            theme::normal()
        };
        rows.push(ListItem::new(Line::from(Span::styled(
            format!("    {label}"),
            style,
        ))));
    }

    let list = List::new(rows).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Actions ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, chunks[1]);

    let help = if app.k8s_manage_input_mode {
        Paragraph::new(Line::from(vec![
            Span::styled(&app.k8s_manage_input_label, theme::highlight()),
            Span::styled(": ", theme::normal()),
            Span::styled(&app.k8s_manage_input_buffer, theme::normal()),
            Span::styled("▎  ", theme::normal()),
            Span::styled("Enter ", theme::highlight()),
            Span::styled("Confirm  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Cancel", theme::muted()),
        ]))
    } else if app.k8s_manage_action_running {
        Paragraph::new(Line::from(vec![
            Span::styled("Running... ", theme::info()),
            Span::styled("l ", theme::highlight()),
            Span::styled("Log  ", theme::normal()),
        ]))
    } else {
        Paragraph::new(Line::from(vec![
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Navigate  ", theme::normal()),
            Span::styled("Enter ", theme::highlight()),
            Span::styled("Run  ", theme::normal()),
            Span::styled("l ", theme::highlight()),
            Span::styled("Log  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    };
    f.render_widget(help, chunks[2]);
}

fn render_log_viewer(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(5),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("K8s Command Output")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let log_height = chunks[1].height.saturating_sub(2) as usize;
    let total_lines = app.k8s_manage_log.len();

    let scroll = if app.k8s_manage_log_autoscroll {
        total_lines.saturating_sub(log_height)
    } else {
        app.k8s_manage_log_scroll
    };

    let visible_lines: Vec<Line> = app
        .k8s_manage_log
        .iter()
        .skip(scroll)
        .take(log_height)
        .map(|line| Line::from(Span::styled(line.as_str(), theme::normal())))
        .collect();

    let log_block = Paragraph::new(visible_lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Output ")
            .title_style(theme::heading()),
    );
    f.render_widget(log_block, chunks[1]);

    if total_lines > log_height {
        let mut scrollbar_state = ScrollbarState::new(total_lines).position(scroll);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            chunks[1].inner(Margin {
                vertical: 1,
                horizontal: 0,
            }),
            &mut scrollbar_state,
        );
    }

    let status = if app.k8s_manage_action_running {
        Paragraph::new(Line::from(vec![
            Span::styled("Running... ", theme::info()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back to menu", theme::muted()),
        ]))
    } else if app.k8s_manage_action_complete {
        Paragraph::new(Line::from(vec![
            Span::styled("Complete ", theme::success()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back to menu  ", theme::muted()),
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Scroll", theme::normal()),
        ]))
    } else {
        Paragraph::new(Line::from(vec![
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back to menu  ", theme::muted()),
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Scroll", theme::normal()),
        ]))
    };
    f.render_widget(status, chunks[2]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.k8s_manage_input_mode {
        handle_input_mode(app, key);
        return;
    }

    if app.k8s_manage_log_visible {
        handle_log_keys(app, key);
        return;
    }

    let actions = flat_actions();
    let action_count = actions.len();

    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.k8s_manage_selected = 0;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.k8s_manage_selected > 0 {
                app.k8s_manage_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.k8s_manage_selected < action_count.saturating_sub(1) {
                app.k8s_manage_selected += 1;
            }
        }
        KeyCode::Char('l') => {
            if !app.k8s_manage_log.is_empty() {
                app.k8s_manage_log_visible = true;
            }
        }
        KeyCode::Enter => {
            if app.k8s_manage_action_running {
                return;
            }
            if let Some(&(_si, _ai, _label, target)) = actions.get(app.k8s_manage_selected) {
                match target {
                    "spot-swap" => {
                        app.k8s_manage_input_mode = true;
                        app.k8s_manage_input_buffer.clear();
                        app.k8s_manage_input_label = "Node class (e.g. gp.vs1.2xlarge)".into();
                    }
                    "spot-price" => {
                        app.k8s_manage_input_mode = true;
                        app.k8s_manage_input_buffer.clear();
                        app.k8s_manage_input_label = "Bid price (e.g. 0.50)".into();
                    }
                    "k8s-gpu-window" => {
                        app.k8s_manage_input_mode = true;
                        app.k8s_manage_input_buffer.clear();
                        app.k8s_manage_input_label = "Minutes (e.g. 30)".into();
                    }
                    "k8s-delete" => {
                        app.k8s_manage_input_mode = true;
                        app.k8s_manage_input_buffer.clear();
                        app.k8s_manage_input_label = "Type 'yes' to confirm delete".into();
                    }
                    _ => {
                        spawn_k8s_action(app, target, None);
                    }
                }
            }
        }
        _ => {}
    }
}

fn handle_input_mode(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.k8s_manage_input_mode = false;
            app.k8s_manage_input_buffer.clear();
        }
        KeyCode::Enter => {
            let input = app.k8s_manage_input_buffer.clone();
            app.k8s_manage_input_mode = false;

            let actions = flat_actions();
            if let Some(&(_si, _ai, _label, target)) = actions.get(app.k8s_manage_selected) {
                match target {
                    "k8s-delete" => {
                        if input.trim().eq_ignore_ascii_case("yes") {
                            spawn_k8s_action(app, target, None);
                        }
                    }
                    "spot-swap" => {
                        if !input.trim().is_empty() {
                            let make_target = format!("spot-swap CLASS={}", input.trim());
                            spawn_k8s_raw_action(app, &make_target);
                        }
                    }
                    "spot-price" => {
                        if !input.trim().is_empty() {
                            let make_target = format!("spot-price BID={}", input.trim());
                            spawn_k8s_raw_action(app, &make_target);
                        }
                    }
                    "k8s-gpu-window" => {
                        if !input.trim().is_empty() {
                            let make_target = format!("k8s-gpu-window MINUTES={}", input.trim());
                            spawn_k8s_raw_action(app, &make_target);
                        }
                    }
                    _ => {
                        spawn_k8s_action(app, target, Some(&input));
                    }
                }
            }

            app.k8s_manage_input_buffer.clear();
        }
        KeyCode::Backspace => {
            app.k8s_manage_input_buffer.pop();
        }
        KeyCode::Char(c) => {
            app.k8s_manage_input_buffer.push(c);
        }
        _ => {}
    }
}

fn handle_log_keys(app: &mut App, key: KeyEvent) {
    let log_height = 20_usize; // approximate visible height

    match key.code {
        KeyCode::Esc | KeyCode::Char('q') => {
            app.k8s_manage_log_visible = false;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            app.k8s_manage_log_autoscroll = false;
            if app.k8s_manage_log_scroll > 0 {
                app.k8s_manage_log_scroll -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            let max = app.k8s_manage_log.len().saturating_sub(log_height);
            if app.k8s_manage_log_scroll < max {
                app.k8s_manage_log_scroll += 1;
            }
            if app.k8s_manage_log_scroll >= max {
                app.k8s_manage_log_autoscroll = true;
            }
        }
        KeyCode::Char('G') => {
            app.k8s_manage_log_autoscroll = true;
        }
        KeyCode::Char('g') => {
            app.k8s_manage_log_scroll = 0;
            app.k8s_manage_log_autoscroll = false;
        }
        _ => {}
    }
}

fn spawn_k8s_action(app: &mut App, make_target: &str, _extra: Option<&str>) {
    spawn_k8s_raw_action(app, make_target);
}

fn spawn_k8s_raw_action(app: &mut App, make_target: &str) {
    let (tx, rx) = mpsc::channel::<K8sManageUpdate>();
    app.k8s_manage_rx = Some(rx);
    app.k8s_manage_log.clear();
    app.k8s_manage_log_visible = true;
    app.k8s_manage_log_scroll = 0;
    app.k8s_manage_log_autoscroll = true;
    app.k8s_manage_action_running = true;
    app.k8s_manage_action_complete = false;

    let repo_root = app.repo_root.clone();
    let target = make_target.to_string();
    let kubeconfig: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.kubeconfig.clone());
    let overlay: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.k8s_overlay.clone());
    let spot_token: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.spot_token.clone());
    let environment: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.environment.clone());

    std::thread::spawn(move || {
        let _ = tx.send(K8sManageUpdate::Log(format!("Running: make {target}")));

        let mut env_prefix = String::new();
        if let Some(ref kc) = kubeconfig {
            env_prefix.push_str(&format!("KUBECONFIG={kc} "));
        }
        if let Some(ref ov) = overlay {
            env_prefix.push_str(&format!("K8S_OVERLAY={ov} "));
        }
        if let Some(ref st) = spot_token {
            env_prefix.push_str(&format!("SPOT_TOKEN={st} "));
        }
        if let Some(ref env) = environment {
            let prefix = match env.as_str() {
                "production" => "prod",
                "development" => "dev",
                _ => "staging",
            };
            env_prefix.push_str(&format!("BUSIBOX_ENV={env} CONTAINER_PREFIX={prefix} "));
        }

        let cmd = format!("{env_prefix}make -C {} {target}", repo_root.display());

        let result = std::process::Command::new("bash")
            .arg("-c")
            .arg(&cmd)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn();

        match result {
            Ok(mut child) => {
                use std::io::BufRead;

                if let Some(stdout) = child.stdout.take() {
                    let reader = std::io::BufReader::new(stdout);
                    for line in reader.lines() {
                        if let Ok(line) = line {
                            let _ = tx.send(K8sManageUpdate::Log(format!("  {line}")));
                        }
                    }
                }

                if let Some(stderr) = child.stderr.take() {
                    let reader = std::io::BufReader::new(stderr);
                    for line in reader.lines() {
                        if let Ok(line) = line {
                            let _ = tx.send(K8sManageUpdate::Log(format!("  {line}")));
                        }
                    }
                }

                let exit_status = child.wait();
                let success = exit_status.as_ref().map(|s| s.success()).unwrap_or(false);
                if success {
                    let _ = tx.send(K8sManageUpdate::Log("✓ Command completed successfully".into()));
                } else {
                    let code = exit_status
                        .ok()
                        .and_then(|s| s.code())
                        .unwrap_or(-1);
                    let _ = tx.send(K8sManageUpdate::Log(format!("✗ Command failed (exit code: {code})")));
                }
                let _ = tx.send(K8sManageUpdate::Complete { success });
            }
            Err(e) => {
                let _ = tx.send(K8sManageUpdate::Log(format!("ERROR: Failed to spawn: {e}")));
                let _ = tx.send(K8sManageUpdate::Complete { success: false });
            }
        }
    });
}

/// Process K8s manage updates from the background thread. Called from the main loop.
pub fn process_k8s_updates(app: &mut App) {
    if let Some(rx) = app.k8s_manage_rx.take() {
        use std::sync::mpsc::TryRecvError;
        let mut put_back = true;

        loop {
            match rx.try_recv() {
                Ok(K8sManageUpdate::Log(line)) => {
                    app.k8s_manage_log.push(line);
                }
                Ok(K8sManageUpdate::Complete { success: _ }) => {
                    app.k8s_manage_action_running = false;
                    app.k8s_manage_action_complete = true;
                    put_back = false;
                    break;
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    app.k8s_manage_action_running = false;
                    app.k8s_manage_action_complete = true;
                    put_back = false;
                    break;
                }
            }
        }

        if put_back {
            app.k8s_manage_rx = Some(rx);
        }
    }
}
