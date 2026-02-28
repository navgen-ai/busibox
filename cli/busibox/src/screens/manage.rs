use crate::app::{App, ManageUpdate, MessageKind, Screen, ServiceStatus, SetupTarget};
use crate::modules::remote;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

fn get_all_services(app: &App) -> Vec<(&'static str, String)> {
    let mut services = vec![
        ("Infrastructure", "postgres".to_string()),
        ("Infrastructure", "redis".to_string()),
        ("Infrastructure", "minio".to_string()),
        ("Infrastructure", "milvus".to_string()),
        ("APIs", "authz".to_string()),
        ("APIs", "agent".to_string()),
        ("APIs", "ingest".to_string()),
        ("APIs", "search".to_string()),
        ("APIs", "deploy".to_string()),
        ("APIs", "docs".to_string()),
        ("APIs", "embedding".to_string()),
    ];
    for svc in app.llm_services() {
        services.push(("LLM", svc.to_string()));
    }
    services.push(("Frontend", "proxy".to_string()));
    services.push(("Frontend", "core-apps".to_string()));
    services
}

pub fn render(f: &mut Frame, app: &App) {
    if app.manage_log_visible {
        render_log_viewer(f, app);
        return;
    }

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(12),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Service Management")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    if app.manage_services.is_empty() {
        let msg = Paragraph::new("Loading service status...")
            .style(theme::info())
            .alignment(Alignment::Center)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim())
                    .title(" Services ")
                    .title_style(theme::heading()),
            );
        f.render_widget(msg, chunks[1]);
    } else {
        let rows: Vec<Row> = app
            .manage_services
            .iter()
            .enumerate()
            .map(|(i, svc)| {
                let status_style = if svc.status.contains("running")
                    || svc.status.contains("healthy")
                    || svc.status.contains("active")
                {
                    theme::success()
                } else if svc.status.contains("stopped") || svc.status.contains("inactive") {
                    theme::warning()
                } else if svc.status.contains("error") || svc.status.contains("failed") {
                    theme::error()
                } else {
                    theme::dim()
                };

                let row_style = if i == app.manage_selected {
                    theme::selected()
                } else {
                    Style::default()
                };

                Row::new(vec![
                    Cell::from(svc.group.clone()).style(theme::muted()),
                    Cell::from(svc.name.clone()).style(theme::normal()),
                    Cell::from(svc.status.clone()).style(status_style),
                ])
                .style(row_style)
            })
            .collect();

        // Calculate visible window to follow selection
        let table_height = chunks[1].height.saturating_sub(4) as usize; // borders + header + margin
        let total_rows = rows.len();
        let scroll_offset = if app.manage_selected >= table_height {
            app.manage_selected - table_height + 1
        } else {
            0
        };
        let visible_rows: Vec<Row> = rows
            .into_iter()
            .skip(scroll_offset)
            .take(table_height)
            .collect();

        let scroll_info = if total_rows > table_height {
            format!(
                " {}-{}/{} ",
                scroll_offset + 1,
                (scroll_offset + table_height).min(total_rows),
                total_rows
            )
        } else {
            String::new()
        };

        let table = Table::new(
            visible_rows,
            [
                Constraint::Length(16),
                Constraint::Min(20),
                Constraint::Length(20),
            ],
        )
        .header(
            Row::new(vec![
                Cell::from("Group").style(theme::muted()),
                Cell::from("Service").style(theme::muted()),
                Cell::from("Status").style(theme::muted()),
            ])
            .bottom_margin(1),
        )
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(format!(" Services{scroll_info}"))
                .title_style(theme::heading()),
        );
        f.render_widget(table, chunks[1]);

        if total_rows > table_height {
            let mut scrollbar_state = ScrollbarState::new(total_rows)
                .position(scroll_offset);
            let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
                .begin_symbol(Some("↑"))
                .end_symbol(Some("↓"));
            f.render_stateful_widget(
                scrollbar,
                chunks[1].inner(Margin { vertical: 1, horizontal: 0 }),
                &mut scrollbar_state,
            );
        }
    }

    let help_text = if app.manage_services.is_empty() {
        " Enter Load  Esc Back"
    } else {
        " r Restart  l Logs  s Stop/Start  d Redeploy  Enter Refresh  Esc Back"
    };
    let help = Paragraph::new(Line::from(Span::styled(help_text, theme::muted())));
    f.render_widget(help, chunks[2]);
}

fn render_log_viewer(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(1),
            Constraint::Min(6),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let svc_name = app
        .manage_services
        .get(app.manage_selected)
        .map(|s| s.name.as_str())
        .unwrap_or("service");

    let title = Paragraph::new(format!("Action Log — {svc_name}"))
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let tick = app.manage_tick;
    let spinner_char = SPINNER[tick % SPINNER.len()];

    let subtitle = if app.manage_action_running {
        Paragraph::new(Line::from(vec![
            Span::styled(format!("{spinner_char} "), theme::info()),
            Span::styled("Running...", theme::info()),
        ]))
        .alignment(Alignment::Center)
    } else if app.manage_action_complete {
        let last = app.manage_log.last().map(|s| s.as_str()).unwrap_or("");
        if last.contains("ERROR") || last.contains("FAILED") || last.contains("failed") {
            Paragraph::new("Action failed")
                .style(theme::error())
                .alignment(Alignment::Center)
        } else {
            Paragraph::new("Action complete")
                .style(theme::success())
                .alignment(Alignment::Center)
        }
    } else {
        Paragraph::new("").alignment(Alignment::Center)
    };
    f.render_widget(subtitle, chunks[1]);

    let log_height = chunks[2].height.saturating_sub(2) as usize;
    let max_scroll = app.manage_log.len().saturating_sub(log_height);
    let scroll = app.manage_log_scroll.min(max_scroll);

    let visible: Vec<Line> = app
        .manage_log
        .iter()
        .skip(scroll)
        .take(log_height)
        .map(|l| {
            let style = if l.contains("ERROR") || l.contains("FAILED") {
                theme::error()
            } else if l.contains("✓") || l.contains("SUCCESS") || l.contains("successful") {
                theme::success()
            } else if l.starts_with("Deploying") || l.starts_with("Running") {
                theme::info()
            } else {
                theme::normal()
            };
            Line::from(Span::styled(l.as_str(), style))
        })
        .collect();

    let scrollbar_info = if app.manage_log.len() > log_height {
        format!(
            " Log ({}-{} of {}) ",
            scroll + 1,
            (scroll + log_height).min(app.manage_log.len()),
            app.manage_log.len()
        )
    } else {
        " Log ".to_string()
    };

    let log_panel = Paragraph::new(visible).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(scrollbar_info)
            .title_style(theme::heading()),
    );
    f.render_widget(log_panel, chunks[2]);

    if app.manage_log.len() > log_height {
        let mut scrollbar_state =
            ScrollbarState::new(app.manage_log.len()).position(scroll);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            chunks[2].inner(Margin {
                vertical: 1,
                horizontal: 0,
            }),
            &mut scrollbar_state,
        );
    }

    let help_text = if app.manage_action_running {
        " ↑/↓ Scroll  (waiting for action to complete...)"
    } else {
        " ↑/↓ Scroll  c Copy  Esc/l Close log viewer"
    };
    let help = Paragraph::new(Line::from(Span::styled(help_text, theme::muted())));
    f.render_widget(help, chunks[3]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.manage_log_visible {
        handle_log_viewer_key(app, key);
        return;
    }

    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.menu_selected = 0;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.manage_selected > 0 {
                app.manage_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.manage_selected < app.manage_services.len().saturating_sub(1) {
                app.manage_selected += 1;
            }
        }
        KeyCode::Enter => {
            load_service_status(app);
        }
        KeyCode::Char('r') => {
            run_action(app, "restart");
        }
        KeyCode::Char('l') => {
            if !app.manage_log.is_empty() {
                app.manage_log_visible = true;
                app.manage_log_scroll = app.manage_log.len().saturating_sub(1);
            } else {
                run_action(app, "logs");
            }
        }
        KeyCode::Char('s') => {
            let current_status = app
                .manage_services
                .get(app.manage_selected)
                .map(|s| s.status.clone())
                .unwrap_or_default();
            if current_status.contains("running")
                || current_status.contains("active")
            {
                run_action(app, "stop");
            } else {
                run_action(app, "start");
            }
        }
        KeyCode::Char('d') => {
            run_action(app, "redeploy");
        }
        _ => {}
    }
}

fn handle_log_viewer_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc | KeyCode::Char('l') => {
            if !app.manage_action_running {
                app.manage_log_visible = false;
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.manage_log_scroll > 0 {
                app.manage_log_scroll -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            app.manage_log_scroll += 1;
        }
        KeyCode::Home => {
            app.manage_log_scroll = 0;
        }
        KeyCode::End => {
            app.manage_log_scroll = app.manage_log.len().saturating_sub(1);
        }
        KeyCode::Char('c') => {
            if !app.manage_action_running {
                let log_text = app.manage_log.join("\n");
                let _ = copy_to_clipboard(&log_text);
                app.set_message("Log copied to clipboard", MessageKind::Info);
            }
        }
        _ => {}
    }
}

pub fn load_service_status(app: &mut App) {
    app.manage_services.clear();

    for (group, name) in get_all_services(app) {
        app.manage_services.push(ServiceStatus {
            name,
            group: group.to_string(),
            status: "checking...".into(),
        });
    }

    // Batch all status checks in one make call (avoids N container startups)
    let is_remote = app.setup_target == SetupTarget::Remote;
    let profile_data = app.active_profile().map(|(_, p)| p.clone());
    let ssh_ref = app.ssh_connection.clone();

    let all_services: Vec<String> = app.manage_services.iter().map(|s| s.name.clone()).collect();
    let service_list = all_services.join(",");

    let batch_output = if is_remote {
        if let Some(ssh) = &ssh_ref {
            let remote_path = profile_data
                .as_ref()
                .map(|p| p.effective_remote_path())
                .unwrap_or_else(|| app.remote_path_input.as_str());
            remote::exec_make_capture(
                ssh,
                remote_path,
                &format!("manage SERVICE={service_list} ACTION=status"),
            )
            .unwrap_or_default()
        } else {
            String::new()
        }
    } else {
        remote::run_local_make_capture(
            &app.repo_root,
            &format!("manage SERVICE={service_list} ACTION=status"),
        )
        .unwrap_or_default()
    };

    // Parse the batch output — each service's status line typically contains the service name
    for svc in &mut app.manage_services {
        let raw_line = batch_output
            .lines()
            .filter(|l| l.contains(&svc.name))
            .last()
            .unwrap_or("unknown")
            .trim()
            .to_string();
        let clean = remote::strip_ansi(&raw_line);
        svc.status = if clean.is_empty() {
            "unknown".into()
        } else {
            clean
        };
    }
}

fn run_action(app: &mut App, action: &str) {
    let svc = match app.manage_services.get(app.manage_selected) {
        Some(s) => s.clone(),
        None => return,
    };

    let is_remote = app.setup_target == SetupTarget::Remote;
    let make_args = format!("manage SERVICE={} ACTION={action}", svc.name);

    // For logs, signal the main loop to run interactively (with TUI suspended)
    if action == "logs" {
        if is_remote {
            if let Some(ssh) = &app.ssh_connection {
                let profile = app.active_profile().map(|(_, p)| p.clone());
                let remote_path = profile
                    .as_ref()
                    .map(|p| p.effective_remote_path())
                    .unwrap_or_else(|| app.remote_path_input.as_str());
                app.pending_interactive_cmd = Some(format!(
                    "REMOTE:{}:{}:cd {} && USE_MANAGER=0 make {}",
                    ssh.host, ssh.key_path, remote_path, make_args
                ));
            } else {
                app.set_message("No SSH connection", MessageKind::Error);
            }
        } else {
            app.pending_interactive_cmd = Some(make_args);
        }
        return;
    }

    // For redeploy and restart, use async worker with log viewer
    if action == "redeploy" || action == "restart" {
        spawn_action_worker(app, &svc.name, action);
        return;
    }

    // For quick actions (stop/start), run synchronously
    let result = if is_remote {
        if let Some(ssh) = &app.ssh_connection {
            let profile = app.active_profile().map(|(_, p)| p.clone());
            let remote_path = profile
                .as_ref()
                .map(|p| p.effective_remote_path())
                .unwrap_or_else(|| app.remote_path_input.as_str());
            remote::exec_make_quiet(ssh, remote_path, &make_args)
        } else {
            Err(color_eyre::eyre::eyre!("No SSH"))
        }
    } else {
        remote::run_local_make_quiet(&app.repo_root, &make_args)
            .map_err(|e| color_eyre::eyre::eyre!("{e}"))
    };

    match result {
        Ok((0, _)) => {
            app.set_message(
                &format!("{} {} successful", action, svc.name),
                MessageKind::Success,
            );
        }
        Ok((code, _)) => {
            app.set_message(
                &format!("{} {} failed (exit {})", action, svc.name, code),
                MessageKind::Error,
            );
        }
        Err(e) => {
            app.set_message(&format!("{action} error: {e}"), MessageKind::Error);
        }
    }

    // Refresh status after action
    load_service_status(app);
}

fn spawn_action_worker(app: &mut App, service_name: &str, action: &str) {
    let (tx, rx) = std::sync::mpsc::channel::<ManageUpdate>();
    app.manage_rx = Some(rx);
    app.manage_log.clear();
    app.manage_log_visible = true;
    app.manage_log_scroll = 0;
    app.manage_action_running = true;
    app.manage_action_complete = false;

    let is_remote = app.setup_target == SetupTarget::Remote;
    let repo_root = app.repo_root.clone();
    let service = service_name.to_string();
    let action = action.to_string();
    let vault_password = app.vault_password.clone();

    let ssh_details: Option<(String, String, String)> =
        app.ssh_connection.as_ref().map(|ssh| {
            (ssh.host.clone(), ssh.user.clone(), ssh.key_path.clone())
        });

    let profile_remote_path: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.clone())
        .map(|p| p.effective_remote_path().to_string());
    let profile_host: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_host().map(|s| s.to_string()));
    let remote_path_input = app.remote_path_input.clone();

    std::thread::spawn(move || {
        let remote_path = profile_remote_path
            .as_deref()
            .unwrap_or(&remote_path_input)
            .to_string();

        let _ = tx.send(ManageUpdate::Log(format!(
            "Running {action} for {service}..."
        )));

        // For redeploy, also rsync first if remote
        if action == "redeploy" && is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let display_host = profile_host.as_deref().unwrap_or(host);
                let _ = tx.send(ManageUpdate::Log(format!(
                    "Syncing files to {display_host}:{remote_path}..."
                )));

                let ssh = crate::modules::ssh::SshConnection::new(
                    display_host, user, key,
                );

                if let Err(e) = remote::ensure_remote_dir(&ssh, &remote_path) {
                    let _ = tx.send(ManageUpdate::Log(format!(
                        "ERROR: Failed to create remote dir: {e}"
                    )));
                    let _ = tx.send(ManageUpdate::Complete { success: false });
                    return;
                }

                if let Err(e) =
                    remote::sync(&repo_root, display_host, user, key, &remote_path)
                {
                    let _ = tx.send(ManageUpdate::Log(format!(
                        "ERROR: rsync failed: {e}"
                    )));
                    let _ = tx.send(ManageUpdate::Complete { success: false });
                    return;
                }
                let _ = tx.send(ManageUpdate::Log("✓ Files synced".into()));
            }
        }

        let make_args = format!("manage SERVICE={service} ACTION={action}");
        let _ = tx.send(ManageUpdate::Log(format!("Running: make {make_args}")));

        let result: color_eyre::Result<(i32, String)> = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(
                    profile_host.as_deref().unwrap_or(host),
                    user,
                    key,
                );
                if let Some(ref vp) = vault_password {
                    remote::exec_make_quiet_with_vault(&ssh, &remote_path, &make_args, vp)
                } else {
                    remote::exec_make_quiet(&ssh, &remote_path, &make_args)
                }
            } else {
                Err(color_eyre::eyre::eyre!("No SSH connection"))
            }
        } else if let Some(ref vp) = vault_password {
            remote::run_local_make_quiet_with_vault(&repo_root, &make_args, vp)
        } else {
            remote::run_local_make_quiet(&repo_root, &make_args)
        };

        match result {
            Ok((0, output)) => {
                for line in output.lines() {
                    let trimmed = line.trim();
                    if !trimmed.is_empty() {
                        let _ = tx.send(ManageUpdate::Log(format!("  {trimmed}")));
                    }
                }
                let _ = tx.send(ManageUpdate::Log(format!(
                    "✓ {action} {service} successful"
                )));
                let _ = tx.send(ManageUpdate::Complete { success: true });
            }
            Ok((code, output)) => {
                for line in output.lines() {
                    let trimmed = line.trim();
                    if !trimmed.is_empty() {
                        let _ = tx.send(ManageUpdate::Log(format!("  {trimmed}")));
                    }
                }
                let _ = tx.send(ManageUpdate::Log(format!(
                    "FAILED: {action} {service} (exit code {code})"
                )));
                let _ = tx.send(ManageUpdate::Complete { success: false });
            }
            Err(e) => {
                let _ = tx.send(ManageUpdate::Log(format!(
                    "ERROR: {action} {service}: {e}"
                )));
                let _ = tx.send(ManageUpdate::Complete { success: false });
            }
        }
    });
}

fn copy_to_clipboard(text: &str) -> std::io::Result<()> {
    use std::io::Write;
    use std::process::{Command, Stdio};

    #[cfg(target_os = "macos")]
    let mut child = Command::new("pbcopy")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;

    #[cfg(target_os = "linux")]
    let mut child = Command::new("xclip")
        .args(["-selection", "clipboard"])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;

    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    return Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "clipboard not supported",
    ));

    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(text.as_bytes())?;
    }
    child.wait()?;
    Ok(())
}
