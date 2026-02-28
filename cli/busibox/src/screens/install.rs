use crate::app::{App, InstallStatus, MessageKind, Screen, ServiceInstallState, SetupTarget};
use crate::modules::remote;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/// Map environment name to container/vault prefix.
/// Must match scripts/make/service-deploy.sh get_container_prefix().
/// Map environment name to container/vault prefix.
/// Must match scripts/make/service-deploy.sh get_container_prefix().
/// Docker can be: development, staging, production, demo
/// Proxmox can be: staging, production
pub fn env_to_prefix(environment: &str) -> String {
    match environment {
        "demo" => "demo",
        "development" => "dev",
        "staging" => "staging",
        "production" => "prod",
        _ => "dev", // default same as service-deploy.sh
    }
    .to_string()
}

fn shell_escape(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn get_bootstrap_stages(_app: &App) -> Vec<(&'static str, &'static str, Vec<String>)> {
    vec![
        ("Prerequisites", "Ansible & Dependencies", vec!["_prerequisites".into()]),
        ("Docker Cleanup", "Stop conflicting containers", vec!["_docker_cleanup".into()]),
        ("Database", "PostgreSQL", vec!["postgres".into()]),
        ("Authentication", "AuthZ API", vec!["authz".into()]),
        ("Deployment", "Deploy API", vec!["deploy".into()]),
        (
            "Portal",
            "Portal & Admin",
            vec!["core-apps".into()],
        ),
    ]
}

pub fn render(f: &mut Frame, app: &App) {
    if app.install_log_visible {
        render_log_viewer(f, app);
        return;
    }

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(1),
            Constraint::Min(6),
            Constraint::Length(1),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Bootstrap Installation")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    if app.install_services.is_empty() {
        let msg = Paragraph::new("Preparing...")
            .style(theme::info())
            .alignment(Alignment::Center);
        f.render_widget(msg, chunks[2]);
        return;
    }

    let any_failed = app.install_services.iter().any(|s| matches!(s.status, InstallStatus::Failed(_)));
    let subtitle = if app.install_complete && any_failed {
        Paragraph::new("Installation finished with errors!")
            .style(theme::error())
            .alignment(Alignment::Center)
    } else if app.install_complete {
        Paragraph::new("Installation complete!")
            .style(theme::success())
            .alignment(Alignment::Center)
    } else {
        Paragraph::new("Installing core services...")
            .style(theme::muted())
            .alignment(Alignment::Center)
    };
    f.render_widget(subtitle, chunks[1]);

    let tick = app.install_tick;
    let spinner_char = SPINNER[tick % SPINNER.len()];

    let mut lines: Vec<Line> = Vec::new();
    lines.push(Line::from(""));

    let stages = get_bootstrap_stages(app);
    for (stage_name, description, services) in &stages {
        let stage_status = aggregate_stage_status(app, services);

        let (icon, style, detail) = match &stage_status {
            InstallStatus::Pending => ("○".to_string(), theme::dim(), description.to_string()),
            InstallStatus::Deploying => (
                spinner_char.to_string(),
                theme::info(),
                format!("{description} deploying..."),
            ),
            InstallStatus::Healthy => (
                "✓".to_string(),
                theme::success(),
                format!("{description} installed"),
            ),
            InstallStatus::Failed(e) => {
                ("✗".to_string(), theme::error(), format!("failed: {e}"))
            }
        };

        lines.push(Line::from(vec![
            Span::styled(format!("  {icon} "), style),
            Span::styled(format!("{stage_name:<16}"), theme::normal()),
            Span::styled(detail, style),
        ]));
    }

    // Model download line
    lines.push(Line::from(""));
    let model_status = if app.install_complete {
        Line::from(vec![
            Span::styled("  ✓ ", theme::success()),
            Span::styled("Models          ", theme::normal()),
            Span::styled("cached", theme::success()),
        ])
    } else {
        Line::from(vec![
            Span::styled(format!("  {} ", spinner_char), theme::info()),
            Span::styled("Models          ", theme::normal()),
            Span::styled("downloading in background...", theme::info()),
        ])
    };
    lines.push(model_status);

    if app.install_complete {
        lines.push(Line::from(""));
        if let Some(url) = &app.install_portal_url {
            lines.push(Line::from(vec![
                Span::styled("  → ", theme::info()),
                Span::styled("Opening ", theme::normal()),
                Span::styled(url.as_str(), theme::highlight()),
            ]));
        }
    }

    let content = Paragraph::new(lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Bootstrap Services ")
            .title_style(theme::heading()),
    );
    f.render_widget(content, chunks[2]);

    // Last log line as mini-status
    let last_log = app
        .install_log
        .last()
        .map(|s| s.as_str())
        .unwrap_or("");
    let log_style = if last_log.contains("ERROR") || last_log.contains("FAILED") {
        theme::error()
    } else {
        theme::dim()
    };
    let log_line = Paragraph::new(Line::from(Span::styled(last_log, log_style)));
    f.render_widget(log_line, chunks[3]);

    let help_text = if app.install_complete && any_failed {
        " r Retry  l View error logs  Enter Continue  Esc Back"
    } else if app.install_complete {
        " Enter Manage Services  l View logs  Esc Back"
    } else {
        " l View logs  Esc Cancel"
    };
    let help_style = if app.install_complete && any_failed {
        theme::warning()
    } else {
        theme::muted()
    };
    let help = Paragraph::new(Line::from(Span::styled(help_text, help_style)));
    f.render_widget(help, chunks[4]);
}

fn render_log_viewer(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(6),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Installation Log")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let log_height = chunks[1].height.saturating_sub(2) as usize;
    let max_scroll = app.install_log.len().saturating_sub(log_height);
    let scroll = app.install_log_scroll.min(max_scroll);

    let visible: Vec<Line> = app
        .install_log
        .iter()
        .skip(scroll)
        .take(log_height)
        .map(|l| {
            let style = if l.contains("ERROR") || l.contains("FAILED") {
                theme::error()
            } else if l.contains("✓") || l.contains("SUCCESS") {
                theme::success()
            } else if l.starts_with("Deploying") || l.starts_with("Syncing") {
                theme::info()
            } else {
                theme::normal()
            };
            Line::from(Span::styled(l.as_str(), style))
        })
        .collect();

    let scrollbar_info = if app.install_log.len() > log_height {
        format!(
            " Log ({}-{} of {}) ",
            scroll + 1,
            (scroll + log_height).min(app.install_log.len()),
            app.install_log.len()
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
    f.render_widget(log_panel, chunks[1]);

    if app.install_log.len() > log_height {
        let mut scrollbar_state = ScrollbarState::new(app.install_log.len())
            .position(scroll);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            chunks[1].inner(Margin { vertical: 1, horizontal: 0 }),
            &mut scrollbar_state,
        );
    }

    let help = Paragraph::new(Line::from(Span::styled(
        " ↑/↓ Scroll  c Copy  l/Esc Close log viewer",
        theme::muted(),
    )));
    f.render_widget(help, chunks[2]);
}

fn aggregate_stage_status(app: &App, services: &[String]) -> InstallStatus {
    let mut any_deploying = false;
    let mut any_failed = false;
    let mut all_healthy = true;
    let mut fail_msg = String::new();

    for svc in &app.install_services {
        if services.contains(&svc.name) {
            match &svc.status {
                InstallStatus::Deploying => {
                    any_deploying = true;
                    all_healthy = false;
                }
                InstallStatus::Failed(e) => {
                    any_failed = true;
                    all_healthy = false;
                    fail_msg = e.clone();
                }
                InstallStatus::Pending => {
                    all_healthy = false;
                }
                InstallStatus::Healthy => {}
            }
        }
    }

    if any_failed {
        InstallStatus::Failed(fail_msg)
    } else if any_deploying {
        InstallStatus::Deploying
    } else if all_healthy && !services.is_empty() {
        InstallStatus::Healthy
    } else {
        InstallStatus::Pending
    }
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.install_log_visible {
        handle_log_viewer_key(app, key);
        return;
    }

    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.menu_selected = 0;
        }
        KeyCode::Char('l') => {
            app.install_log_visible = true;
            app.install_log_scroll = app.install_log.len().saturating_sub(1);
        }
        KeyCode::Char('r') => {
            if app.install_complete {
                let any_failed = app.install_services.iter().any(|s| matches!(s.status, InstallStatus::Failed(_)));
                if any_failed {
                    // Reset and re-run install
                    app.install_complete = false;
                    app.install_portal_url = None;
                    app.install_rx = None;
                    // Don't clear log - append to it
                    app.install_log.push("".into());
                    app.install_log.push("═══ Retrying installation... ═══".into());
                    app.install_log.push("".into());
                    // Reset failed/pending services back to pending
                    for svc in app.install_services.iter_mut() {
                        if !matches!(svc.status, InstallStatus::Healthy) {
                            svc.status = InstallStatus::Pending;
                        }
                    }
                    spawn_install_worker(app);
                }
            }
        }
        KeyCode::Enter => {
            if app.install_complete {
                app.screen = Screen::Manage;
            }
        }
        _ => {}
    }
}

fn handle_log_viewer_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc | KeyCode::Char('l') => {
            app.install_log_visible = false;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.install_log_scroll > 0 {
                app.install_log_scroll -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            app.install_log_scroll += 1;
        }
        KeyCode::Home => {
            app.install_log_scroll = 0;
        }
        KeyCode::End => {
            app.install_log_scroll = app.install_log.len().saturating_sub(1);
        }
        KeyCode::Char('c') => {
            // Copy log to clipboard
            let log_text = app.install_log.join("\n");
            let _ = copy_to_clipboard(&log_text);
            app.set_message("Log copied to clipboard", MessageKind::Info);
        }
        _ => {}
    }
}

fn copy_to_clipboard(text: &str) -> std::io::Result<()> {
    use std::io::Write;
    use std::process::{Command, Stdio};

    // Use pbcopy on macOS, xclip on Linux
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

pub fn auto_start(app: &mut App) {
    init_install(app);

    // If we already have a vault password (from a completed vault setup), proceed
    if app.vault_password.is_some() {
        spawn_install_worker(app);
        return;
    }

    // Otherwise trigger vault setup before installing
    app.pending_vault_setup = true;
}

/// Public entry point to start the install worker after vault setup completes.
pub fn spawn_install_worker_pub(app: &mut App) {
    spawn_install_worker(app);
}

fn init_install(app: &mut App) {
    app.install_services.clear();
    app.install_log.clear();
    app.install_complete = false;
    app.install_portal_url = None;
    app.install_tick = 0;

    let stages = get_bootstrap_stages(app);
    for (_, _, services) in &stages {
        for svc in services {
            app.install_services.push(ServiceInstallState {
                name: svc.clone(),
                group: String::new(),
                status: InstallStatus::Pending,
            });
        }
    }
}

fn spawn_install_worker(app: &mut App) {
    use crate::app::InstallUpdate;

    let (tx, rx) = std::sync::mpsc::channel::<InstallUpdate>();
    app.install_rx = Some(rx);

    let is_remote = app.setup_target == SetupTarget::Remote;
    let repo_root = app.repo_root.clone();
    let remote_path_input = app.remote_path_input.clone();
    let vault_password: Option<String> = app.vault_password.clone();
    let clean_install = app.clean_install;
    // Derive vault prefix from environment (same logic as service-deploy.sh get_container_prefix)
    let vault_prefix: String = app
        .active_profile()
        .map(|(_, p)| env_to_prefix(&p.environment))
        .unwrap_or_else(|| "dev".into());

    let ssh_details: Option<(String, String, String)> = app.ssh_connection.as_ref().map(|ssh| {
        (ssh.host.clone(), ssh.user.clone(), ssh.key_path.clone())
    });

    let profile_remote_path: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.clone())
        .map(|p| p.effective_remote_path().to_string());
    let profile_host: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_host().map(|s| s.to_string()));
    let admin_email: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.admin_email.clone());
    let profile_model_tier: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_model_tier().map(|t| t.name().to_string()));
    let profile_llm_backend: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.hardware.as_ref().map(|h| match h.llm_backend {
            crate::modules::hardware::LlmBackend::Mlx => "mlx".to_string(),
            crate::modules::hardware::LlmBackend::Vllm => "vllm".to_string(),
            crate::modules::hardware::LlmBackend::Cloud => "cloud".to_string(),
        }));

    let stages: Vec<(String, String, Vec<String>)> = get_bootstrap_stages(app)
        .into_iter()
        .map(|(name, desc, svcs)| (name.to_string(), desc.to_string(), svcs))
        .collect();

    let already_healthy: std::collections::HashSet<String> = app
        .install_services
        .iter()
        .filter(|s| matches!(s.status, InstallStatus::Healthy))
        .map(|s| s.name.clone())
        .collect();

    std::thread::spawn(move || {
        use crate::app::InstallUpdate;
        use crate::app::InstallStatus;

        let remote_path = profile_remote_path
            .as_deref()
            .unwrap_or(&remote_path_input)
            .to_string();

        if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(
                    profile_host.as_deref().unwrap_or(host),
                    user,
                    key,
                );
                let display_host = profile_host.as_deref().unwrap_or(host);

                let _ = tx.send(InstallUpdate::Log(format!(
                    "Syncing files to {display_host}:{remote_path}..."
                )));

                if let Err(e) = remote::ensure_remote_dir(&ssh, &remote_path) {
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "ERROR: Failed to create remote dir: {e}"
                    )));
                    let _ = tx.send(InstallUpdate::Complete {
                        portal_url: None,
                    });
                    return;
                }

                if let Err(e) = remote::sync(
                    &repo_root,
                    display_host,
                    user,
                    key,
                    &remote_path,
                ) {
                    let _ = tx.send(InstallUpdate::Log(format!("ERROR: rsync failed: {e}")));
                    let _ = tx.send(InstallUpdate::Complete {
                        portal_url: None,
                    });
                    return;
                }
                let _ = tx.send(InstallUpdate::Log("✓ Files synced".into()));
            }
        }

        // For clean installs, delete existing vault so it gets recreated fresh from example
        if clean_install {
            if let Some(ref _vp) = vault_password {
                let vault_rel = format!(
                    "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
                );

                if is_remote {
                    if let Some((ref host, ref user, ref key)) = ssh_details {
                        let ssh = crate::modules::ssh::SshConnection::new(
                            profile_host.as_deref().unwrap_or(host),
                            user,
                            key,
                        );
                        let remote_path = profile_remote_path
                            .as_deref()
                            .unwrap_or(&remote_path_input);
                        let del_cmd = format!(
                            "cd {} && rm -f {}",
                            remote_path, vault_rel
                        );
                        let _ = ssh.run(&del_cmd);
                        let _ = tx.send(InstallUpdate::Log(
                            "  Clean install: removed existing vault file".into(),
                        ));
                    }
                } else {
                    let vault_path = repo_root.join(&vault_rel);
                    if vault_path.exists() {
                        let _ = std::fs::remove_file(&vault_path);
                        let _ = tx.send(InstallUpdate::Log(
                            "  Clean install: removed existing vault file".into(),
                        ));
                    }
                }
            }
        }

        // Verify vault password matches the vault file on the remote (or local)
        if let Some(ref vp) = vault_password {
            let _ = tx.send(InstallUpdate::Log("Verifying vault password...".into()));

            let vault_rel = format!(
                "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
            );
            let example_rel = format!(
                "provision/ansible/roles/secrets/vars/vault.example.yml"
            );
            let _ = tx.send(InstallUpdate::Log(format!(
                "  vault_prefix={vault_prefix}, vault_rel={vault_rel}"
            )));
            let _ = tx.send(InstallUpdate::Log(format!(
                "  password first 10 chars: {}...", &vp[..vp.len().min(10)]
            )));

            if is_remote {
                if let Some((ref host, ref user, ref key)) = ssh_details {
                    let ssh = crate::modules::ssh::SshConnection::new(
                        profile_host.as_deref().unwrap_or(host),
                        user,
                        key,
                    );

                    // Check if vault file exists
                    let check_exists = format!(
                        "cd {} && [ -f {} ] && echo EXISTS || echo MISSING",
                        remote_path, vault_rel
                    );
                    let vault_exists = ssh
                        .run(&check_exists)
                        .map(|o| o.trim() == "EXISTS")
                        .unwrap_or(false);

                    let _ = tx.send(InstallUpdate::Log(format!(
                        "  vault exists on remote: {vault_exists}"
                    )));

                    if vault_exists {
                        // Test decryption AND output content to prove it works
                        let test_cmd = format!(
                            "for d in \"$HOME/.local/bin\" \"$HOME/Library/Python\"/*/bin /usr/local/bin; do [ -d \"$d\" ] && export PATH=\"$d:$PATH\"; done; \
                             cd {} && \
                             TMPF=$(mktemp) && printf '%s' '{}' > \"$TMPF\" && chmod 600 \"$TMPF\" && \
                             PWLEN=$(wc -c < \"$TMPF\" | tr -d ' ') && \
                             echo \"PWFILE_LEN=$PWLEN\" && \
                             echo \"PWFILE_FIRST10=$(head -c 10 \"$TMPF\")\" && \
                             echo \"VAULT_REALPATH=$(realpath {} 2>/dev/null || echo {})\" && \
                             echo \"VAULT_HEADER=$(head -1 {})\" && \
                             DECRYPTED=$(ansible-vault view {} --vault-password-file=\"$TMPF\" 2>&1) && \
                             echo \"DECRYPT_OK=YES\" && \
                             echo \"CONTENT_LINE2=$(echo \"$DECRYPTED\" | sed -n '2p')\" || \
                             echo \"DECRYPT_OK=NO: $DECRYPTED\"; \
                             rm -f \"$TMPF\"",
                            remote_path,
                            vp.replace('\'', "'\\''"),
                            vault_rel, vault_rel, vault_rel,
                            vault_rel
                        );

                        match ssh.run(&test_cmd) {
                            Ok(output) => {
                                // Log the full diagnostic output
                                for line in output.lines() {
                                    let _ = tx.send(InstallUpdate::Log(format!("  [vault-cli] {}", line)));
                                }

                                let decrypt_ok = output.contains("DECRYPT_OK=YES");

                                if decrypt_ok {
                                    let _ = tx.send(InstallUpdate::Log(
                                        "✓ Vault password verified (decryption test passed)".into(),
                                    ));
                                } else {
                                    let _ = tx.send(InstallUpdate::Log(
                                        "⚠ Vault password mismatch — recreating vault file...".into(),
                                    ));

                                    // Delete and recreate from example
                                    let recreate_cmd = format!(
                                        "for d in \"$HOME/.local/bin\" \"$HOME/Library/Python\"/*/bin /usr/local/bin; do [ -d \"$d\" ] && export PATH=\"$d:$PATH\"; done; \
                                         cd {} && \
                                         rm -f {} && \
                                         cp {} {} && \
                                         TMPF=$(mktemp) && printf '%s' '{}' > \"$TMPF\" && chmod 600 \"$TMPF\" && \
                                         ansible-vault encrypt {} --vault-password-file=\"$TMPF\" && \
                                         rm -f \"$TMPF\"",
                                        remote_path,
                                        vault_rel,
                                        example_rel,
                                        vault_rel,
                                        vp.replace('\'', "'\\''"),
                                        vault_rel
                                    );

                                    match ssh.run(&recreate_cmd) {
                                        Ok(_) => {
                                            let _ = tx.send(InstallUpdate::Log(
                                                "✓ Vault file recreated with correct password".into(),
                                            ));
                                        }
                                        Err(e) => {
                                            let _ = tx.send(InstallUpdate::Log(format!(
                                                "ERROR: Failed to recreate vault: {e}"
                                            )));
                                            let _ = tx.send(InstallUpdate::Complete {
                                                portal_url: None,
                                            });
                                            return;
                                        }
                                    }
                                }
                            }
                            Err(e) => {
                                // ssh.run() returned Err - the command itself failed
                                let _ = tx.send(InstallUpdate::Log(format!(
                                    "  [vault-cli] SSH command error: {e}"
                                )));
                                let _ = tx.send(InstallUpdate::Log(
                                    "⚠ Vault password mismatch — recreating vault file...".into(),
                                ));

                                // Delete and recreate from example
                                let recreate_cmd = format!(
                                    "for d in \"$HOME/.local/bin\" \"$HOME/Library/Python\"/*/bin /usr/local/bin; do [ -d \"$d\" ] && export PATH=\"$d:$PATH\"; done; \
                                     cd {} && \
                                     rm -f {} && \
                                     cp {} {} && \
                                     TMPF=$(mktemp) && printf '%s' '{}' > \"$TMPF\" && chmod 600 \"$TMPF\" && \
                                     ansible-vault encrypt {} --vault-password-file=\"$TMPF\" && \
                                     rm -f \"$TMPF\"",
                                    remote_path,
                                    vault_rel,
                                    example_rel,
                                    vault_rel,
                                    vp.replace('\'', "'\\''"),
                                    vault_rel
                                );

                                match ssh.run(&recreate_cmd) {
                                    Ok(_) => {
                                        let _ = tx.send(InstallUpdate::Log(
                                            "✓ Vault file recreated with correct password".into(),
                                        ));
                                    }
                                    Err(e) => {
                                        let _ = tx.send(InstallUpdate::Log(format!(
                                            "ERROR: Failed to recreate vault: {e}"
                                        )));
                                        let _ = tx.send(InstallUpdate::Complete {
                                            portal_url: None,
                                        });
                                        return;
                                    }
                                }
                            }
                        }
                    } else {
                        // Vault doesn't exist - create it from example
                        let _ = tx.send(InstallUpdate::Log(
                            "Creating vault file from example...".into(),
                        ));
                        let create_cmd = format!(
                            "for d in \"$HOME/.local/bin\" \"$HOME/Library/Python\"/*/bin /usr/local/bin; do [ -d \"$d\" ] && export PATH=\"$d:$PATH\"; done; \
                             cd {} && \
                             cp {} {} && \
                             TMPF=$(mktemp) && printf '%s' '{}' > \"$TMPF\" && chmod 600 \"$TMPF\" && \
                             ansible-vault encrypt {} --vault-password-file=\"$TMPF\" && \
                             rm -f \"$TMPF\"",
                            remote_path,
                            example_rel,
                            vault_rel,
                            vp.replace('\'', "'\\''"),
                            vault_rel
                        );
                        match ssh.run(&create_cmd) {
                            Ok(_) => {
                                let _ = tx.send(InstallUpdate::Log(
                                    "✓ Vault file created and encrypted".into(),
                                ));
                            }
                            Err(e) => {
                                let _ = tx.send(InstallUpdate::Log(format!(
                                    "ERROR: Failed to create vault: {e}"
                                )));
                                let _ = tx.send(InstallUpdate::Complete {
                                    portal_url: None,
                                });
                                return;
                            }
                        }
                    }
                }
            } else {
                // Local: similar logic but with local commands
                let vault_path = repo_root.join(format!(
                    "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
                ));
                let example_path =
                    repo_root.join("provision/ansible/roles/secrets/vars/vault.example.yml");

                if vault_path.exists() {
                    // Test decryption locally
                    let mut tmpfile = std::env::temp_dir();
                    tmpfile.push(format!("busibox-vtest-{}", std::process::id()));
                    let _ = std::fs::write(&tmpfile, vp.as_bytes());
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        let _ = std::fs::set_permissions(&tmpfile, std::fs::Permissions::from_mode(0o600));
                    }

                    let test_result = std::process::Command::new("ansible-vault")
                        .args([
                            "view",
                            &vault_path.to_string_lossy(),
                            "--vault-password-file",
                            &tmpfile.to_string_lossy(),
                        ])
                        .stdout(std::process::Stdio::null())
                        .stderr(std::process::Stdio::null())
                        .status();

                    let _ = std::fs::remove_file(&tmpfile);

                    let can_decrypt = test_result.map(|s| s.success()).unwrap_or(false);

                    if !can_decrypt {
                        let _ = tx.send(InstallUpdate::Log(
                            "⚠ Vault password mismatch — recreating vault file...".into(),
                        ));
                        let _ = std::fs::remove_file(&vault_path);
                        if let Err(e) = std::fs::copy(&example_path, &vault_path) {
                            let _ = tx.send(InstallUpdate::Log(format!("ERROR: {e}")));
                            let _ = tx.send(InstallUpdate::Complete {
                                portal_url: None,
                            });
                            return;
                        }

                        let mut tmpfile = std::env::temp_dir();
                        tmpfile.push(format!("busibox-venc-{}", std::process::id()));
                        let _ = std::fs::write(&tmpfile, vp.as_bytes());
                        #[cfg(unix)]
                        {
                            use std::os::unix::fs::PermissionsExt;
                            let _ = std::fs::set_permissions(&tmpfile, std::fs::Permissions::from_mode(0o600));
                        }
                        let enc_result = std::process::Command::new("ansible-vault")
                            .args([
                                "encrypt",
                                &vault_path.to_string_lossy(),
                                "--vault-password-file",
                                &tmpfile.to_string_lossy(),
                            ])
                            .status();
                        let _ = std::fs::remove_file(&tmpfile);

                        match enc_result {
                            Ok(s) if s.success() => {
                                let _ = tx.send(InstallUpdate::Log(
                                    "✓ Vault file recreated".into(),
                                ));
                            }
                            _ => {
                                let _ = tx.send(InstallUpdate::Log(
                                    "ERROR: Failed to encrypt vault".into(),
                                ));
                                let _ = tx.send(InstallUpdate::Complete {
                                    portal_url: None,
                                });
                                return;
                            }
                        }
                    } else {
                        let _ = tx.send(InstallUpdate::Log(
                            "✓ Vault password verified".into(),
                        ));
                    }
                } else if example_path.exists() {
                    let _ = tx.send(InstallUpdate::Log(
                        "Creating vault file from example...".into(),
                    ));
                    if let Err(e) = std::fs::copy(&example_path, &vault_path) {
                        let _ = tx.send(InstallUpdate::Log(format!("ERROR: {e}")));
                        let _ = tx.send(InstallUpdate::Complete {
                            portal_url: None,
                        });
                        return;
                    }
                    let mut tmpfile = std::env::temp_dir();
                    tmpfile.push(format!("busibox-venc-{}", std::process::id()));
                    let _ = std::fs::write(&tmpfile, vp.as_bytes());
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        let _ = std::fs::set_permissions(&tmpfile, std::fs::Permissions::from_mode(0o600));
                    }
                    let enc_result = std::process::Command::new("ansible-vault")
                        .args([
                            "encrypt",
                            &vault_path.to_string_lossy(),
                            "--vault-password-file",
                            &tmpfile.to_string_lossy(),
                        ])
                        .status();
                    let _ = std::fs::remove_file(&tmpfile);
                    match enc_result {
                        Ok(s) if s.success() => {
                            let _ = tx.send(InstallUpdate::Log(
                                "✓ Vault file created".into(),
                            ));
                        }
                        _ => {
                            let _ = tx.send(InstallUpdate::Log(
                                "ERROR: Failed to encrypt vault".into(),
                            ));
                        }
                    }
                }
            }
        }

        // === Generate secrets for CHANGE_ME placeholders ===
        if let Some(ref vp) = vault_password {
            let _ = tx.send(InstallUpdate::Log("Generating vault secrets...".into()));

            let vault_rel = format!(
                "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
            );

            let admin_email_sed = admin_email
                .as_deref()
                .filter(|e| !e.is_empty())
                .map(|email| format!(r#"    -e "s/CHANGE_ME_ADMIN_EMAILS/{email}/g" \"#))
                .unwrap_or_default();

            // Script: decrypt vault, replace CHANGE_ME placeholders with random values, re-encrypt
            let secrets_script = format!(
                r#"set -e
VAULT_FILE="{vault_rel}"
PW_FILE=$(mktemp)
printf '%s' '{pw}' > "$PW_FILE"
chmod 600 "$PW_FILE"
trap 'rm -f "$PW_FILE"' EXIT

# Check if vault has any CHANGE_ME placeholders
CONTENT=$(ansible-vault view "$VAULT_FILE" --vault-password-file="$PW_FILE" 2>/dev/null)
if ! echo "$CONTENT" | grep -q 'CHANGE_ME'; then
    echo "✓ All secrets already configured"
    exit 0
fi

# Decrypt in-place
ansible-vault decrypt "$VAULT_FILE" --vault-password-file="$PW_FILE"

# Helper: generate a random alphanumeric string
gen() {{ openssl rand -base64 "$1" | tr -d '/+=' | head -c "$1"; }}

# Generate secrets matching vault.sh setup_vault_secrets patterns
PG_PASS=$(gen 24)
MINIO_PASS=$(gen 24)
JWT=$(gen 32)
AUTHZ_KEY=$(gen 32)
LITELLM_API=$(gen 16)
LITELLM_MASTER=$(openssl rand -hex 16)
LITELLM_SALT=$(gen 32)

# sed -i.bak works on both macOS and Linux
sed -i.bak \
    -e "s/CHANGE_ME_POSTGRES_PASSWORD/$PG_PASS/g" \
    -e "s/CHANGE_ME_MINIO_ROOT_USER/minioadmin/g" \
    -e "s/CHANGE_ME_MINIO_ROOT_PASSWORD/$MINIO_PASS/g" \
    -e "s/CHANGE_ME_JWT_SECRET_32_BYTES/$JWT/g" \
    -e "s/CHANGE_ME_SESSION_SECRET_32_BYTES/$JWT/g" \
    -e "s/CHANGE_ME_AUTHZ_MASTER_KEY_32_BYTES/$AUTHZ_KEY/g" \
    -e "s/CHANGE_ME_LITELLM_API_KEY/$LITELLM_API/g" \
    -e "s/CHANGE_ME_LITELLM_MASTER_KEY/$LITELLM_MASTER/g" \
    -e "s/CHANGE_ME_LITELLM_SALT_KEY/$LITELLM_SALT/g" \
{admin_email_sed}    "$VAULT_FILE"
rm -f "${{VAULT_FILE}}.bak"

# Count how many CHANGE_ME remain (optional/non-critical ones like SMTP, GitHub)
REMAINING=$(grep -c 'CHANGE_ME' "$VAULT_FILE" 2>/dev/null || echo 0)

# Re-encrypt
ansible-vault encrypt "$VAULT_FILE" --vault-password-file="$PW_FILE"

echo "✓ Generated 9 bootstrap secrets ($REMAINING optional placeholders remain)"
"#,
                vault_rel = vault_rel,
                pw = vp.replace('\'', "'\\''"),
                admin_email_sed = admin_email_sed,
            );

            let gen_result: color_eyre::Result<(i32, String)> = if is_remote {
                if let Some((ref host, ref user, ref key)) = ssh_details {
                    let ssh = crate::modules::ssh::SshConnection::new(
                        profile_host.as_deref().unwrap_or(host),
                        user,
                        key,
                    );
                    let full_cmd = format!(
                        "for d in \"$HOME/.local/bin\" \"$HOME/Library/Python\"/*/bin /usr/local/bin; do [ -d \"$d\" ] && export PATH=\"$d:$PATH\"; done; \
                         cd {} && bash -c {}",
                        remote_path,
                        shell_escape(&secrets_script)
                    );
                    let mut args: Vec<String> = vec![
                        "-o".into(), "BatchMode=yes".into(),
                        "-o".into(), "StrictHostKeyChecking=accept-new".into(),
                        "-o".into(), "ConnectTimeout=10".into(),
                    ];
                    let key_expanded = crate::modules::ssh::shellexpand_path(key);
                    if !key_expanded.is_empty()
                        && std::path::Path::new(&key_expanded).exists()
                    {
                        args.push("-i".into());
                        args.push(key_expanded);
                    }
                    args.push(ssh.ssh_target());
                    args.push(full_cmd);
                    match std::process::Command::new("ssh").args(&args).output() {
                        Ok(output) => {
                            let exit_code = output.status.code().unwrap_or(1);
                            let combined = format!(
                                "{}{}",
                                String::from_utf8_lossy(&output.stdout),
                                String::from_utf8_lossy(&output.stderr)
                            );
                            Ok((exit_code, remote::strip_ansi(&combined)))
                        }
                        Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                    }
                } else {
                    Err(color_eyre::eyre::eyre!("No SSH connection"))
                }
            } else {
                match std::process::Command::new("bash")
                    .arg("-c")
                    .arg(&secrets_script)
                    .current_dir(&repo_root)
                    .output()
                {
                    Ok(output) => {
                        let exit_code = output.status.code().unwrap_or(1);
                        let combined = format!(
                            "{}{}",
                            String::from_utf8_lossy(&output.stdout),
                            String::from_utf8_lossy(&output.stderr)
                        );
                        Ok((exit_code, remote::strip_ansi(&combined)))
                    }
                    Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                }
            };

            match gen_result {
                Ok((0, output)) => {
                    for line in output.lines() {
                        let trimmed = line.trim();
                        if !trimmed.is_empty() {
                            let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                        }
                    }
                }
                Ok((code, output)) => {
                    for line in output.lines() {
                        let trimmed = line.trim();
                        if !trimmed.is_empty() {
                            let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                        }
                    }
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "WARNING: Secret generation had issues (exit code {code}) — continuing anyway"
                    )));
                }
                Err(e) => {
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "WARNING: Secret generation failed: {e} — continuing anyway"
                    )));
                }
            }
        }

        let _ = tx.send(InstallUpdate::Log(
            "Starting model download in background...".into(),
        ));
        let dl_tier = profile_model_tier.clone();
        let dl_backend = profile_llm_backend.clone();
        let model_download_handle: Option<std::thread::JoinHandle<i32>> = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let host = host.clone();
                let user = user.clone();
                let key = key.clone();
                let rp = remote_path.clone();
                Some(std::thread::spawn(move || -> i32 {
                    let ssh_conn =
                        crate::modules::ssh::SshConnection::new(&host, &user, &key);
                    let mut env_prefix = String::new();
                    if let Some(ref tier) = dl_tier {
                        env_prefix.push_str(&format!("LLM_TIER={tier} "));
                    }
                    if let Some(ref backend) = dl_backend {
                        env_prefix.push_str(&format!("LLM_BACKEND={backend} "));
                    }
                    let cmd = format!(
                        "cd {} && {env_prefix}bash scripts/llm/download-models.sh 2>&1", rp
                    );
                    ssh_conn.run(&cmd).map(|_| 0).unwrap_or(1)
                }))
            } else {
                None
            }
        } else {
            let repo = repo_root.clone();
            Some(std::thread::spawn(move || -> i32 {
                let script = repo.join("scripts/llm/download-models.sh");
                if !script.exists() {
                    return 1;
                }
                let mut cmd = std::process::Command::new("bash");
                cmd.arg(&script)
                    .current_dir(&repo)
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null());
                if let Some(ref tier) = dl_tier {
                    cmd.env("LLM_TIER", tier);
                }
                if let Some(ref backend) = dl_backend {
                    cmd.env("LLM_BACKEND", backend);
                }
                cmd.status()
                    .map(|s| if s.success() { 0 } else { 1 })
                    .unwrap_or(1)
            }))
        };

        let mut any_failed = false;
        for (stage_name, _description, services) in &stages {
            // Skip stages where all services are already healthy
            let stage_services_to_deploy: Vec<String> = services
                .iter()
                .filter(|s| !already_healthy.contains(*s))
                .cloned()
                .collect();
            if stage_services_to_deploy.is_empty() {
                let _ = tx.send(InstallUpdate::Log(format!(
                    "✓ {stage_name} already installed, skipping"
                )));
                continue;
            }

            // Handle prerequisites specially - install Ansible etc. instead of make install
            if stage_services_to_deploy.len() == 1
                && stage_services_to_deploy.first().map(|s| s.as_str()) == Some("_prerequisites")
            {
                let _ = tx.send(InstallUpdate::Log(
                    "Checking and installing prerequisites...".into(),
                ));
                for svc in services {
                    let _ = tx.send(InstallUpdate::ServiceStatus {
                        name: svc.clone(),
                        status: InstallStatus::Deploying,
                    });
                }

                let prereq_script = r#"
                    set -e
                    # Expand PATH to include common pip install locations
                    for pydir in "$HOME/.local/bin" "$HOME/Library/Python"/*/bin /usr/local/bin; do
                        [ -d "$pydir" ] && export PATH="$pydir:$PATH"
                    done
                    # Ensure pip is available
                    if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
                        echo "Installing pip..."
                        if command -v apt-get &>/dev/null; then
                            apt-get update -qq && apt-get install -y -qq python3-pip 2>&1
                        elif command -v yum &>/dev/null; then
                            yum install -y python3-pip 2>&1
                        elif command -v brew &>/dev/null; then
                            brew install python3 2>&1
                        fi
                    fi
                    PIP=$(command -v pip3 2>/dev/null || command -v pip 2>/dev/null || echo pip3)
                    # Install ansible if not present
                    if ! command -v ansible-playbook &>/dev/null; then
                        echo "Installing Ansible..."
                        $PIP install --quiet ansible 2>&1
                        # Re-expand PATH after install (pip may have created new dirs)
                        for pydir in "$HOME/.local/bin" "$HOME/Library/Python"/*/bin /usr/local/bin; do
                            [ -d "$pydir" ] && export PATH="$pydir:$PATH"
                        done
                    fi
                    if ! command -v ansible-vault &>/dev/null; then
                        echo "Installing Ansible (vault missing)..."
                        $PIP install --quiet ansible 2>&1
                        for pydir in "$HOME/.local/bin" "$HOME/Library/Python"/*/bin /usr/local/bin; do
                            [ -d "$pydir" ] && export PATH="$pydir:$PATH"
                        done
                    fi
                    # Verify Ansible
                    if command -v ansible-playbook &>/dev/null; then
                        echo "✓ ansible-playbook: $(ansible-playbook --version | head -1)"
                    else
                        echo "ERROR: ansible-playbook still not found after install"
                        echo "Searched PATH: $PATH"
                        exit 1
                    fi

                    # === Docker checks ===
                    # Check if docker is installed
                    if ! command -v docker &>/dev/null; then
                        echo "ERROR: Docker is not installed."
                        echo "Please install Docker Desktop or Docker Engine on this machine."
                        echo "  macOS: https://docs.docker.com/desktop/install/mac-install/"
                        echo "  Linux: https://docs.docker.com/engine/install/"
                        exit 1
                    fi
                    echo "✓ docker: $(docker --version)"

                    # Check if Docker daemon is running
                    if ! docker info &>/dev/null; then
                        echo "Docker daemon not running — attempting to start..."
                        # macOS: try open Docker Desktop
                        if [ "$(uname)" = "Darwin" ]; then
                            if [ -d "/Applications/Docker.app" ]; then
                                open -a Docker 2>/dev/null || true
                                echo "  Waiting for Docker Desktop to start..."
                            elif [ -d "$HOME/Applications/Docker.app" ]; then
                                open -a "$HOME/Applications/Docker.app" 2>/dev/null || true
                                echo "  Waiting for Docker Desktop to start..."
                            else
                                echo "ERROR: Docker Desktop not found in Applications"
                                exit 1
                            fi
                        else
                            # Linux: try systemctl
                            if command -v systemctl &>/dev/null; then
                                sudo systemctl start docker 2>/dev/null || true
                                echo "  Starting docker service..."
                            elif command -v service &>/dev/null; then
                                sudo service docker start 2>/dev/null || true
                                echo "  Starting docker service..."
                            fi
                        fi
                        # Wait for Docker to be ready (up to 60 seconds)
                        WAITED=0
                        while ! docker info &>/dev/null; do
                            sleep 2
                            WAITED=$((WAITED + 2))
                            if [ $WAITED -ge 60 ]; then
                                echo "ERROR: Docker daemon did not start within 60 seconds"
                                echo "Please start Docker manually and retry."
                                exit 1
                            fi
                            if [ $((WAITED % 10)) -eq 0 ]; then
                                echo "  Still waiting... (${WAITED}s)"
                            fi
                        done
                        echo "✓ Docker daemon started (took ${WAITED}s)"
                    else
                        echo "✓ Docker daemon running"
                    fi

                    # Check docker compose
                    if docker compose version &>/dev/null; then
                        echo "✓ docker compose: $(docker compose version --short 2>/dev/null || docker compose version)"
                    elif command -v docker-compose &>/dev/null; then
                        echo "✓ docker-compose: $(docker-compose --version)"
                    else
                        echo "ERROR: docker compose is not available"
                        echo "Please install Docker Compose v2 or update Docker Desktop."
                        exit 1
                    fi

                    echo "✓ Prerequisites installed"
                "#;

                let result: color_eyre::Result<(i32, String)> = if is_remote {
                    if let Some((ref host, ref user, ref key)) = ssh_details {
                        let ssh = crate::modules::ssh::SshConnection::new(
                            profile_host.as_deref().unwrap_or(host),
                            user,
                            key,
                        );
                        let full_cmd = format!(
                            "for d in \"$HOME/.local/bin\" \"$HOME/Library/Python\"/*/bin /usr/local/bin; do [ -d \"$d\" ] && export PATH=\"$d:$PATH\"; done; bash -c {}",
                            shell_escape(prereq_script)
                        );
                        let mut args: Vec<String> = vec![
                            "-o".into(),
                            "BatchMode=yes".into(),
                            "-o".into(),
                            "StrictHostKeyChecking=accept-new".into(),
                            "-o".into(),
                            "ConnectTimeout=10".into(),
                        ];
                        let key_expanded =
                            crate::modules::ssh::shellexpand_path(key);
                        if !key_expanded.is_empty()
                            && std::path::Path::new(&key_expanded).exists()
                        {
                            args.push("-i".into());
                            args.push(key_expanded);
                        }
                        args.push(ssh.ssh_target());
                        args.push(full_cmd);
                        match std::process::Command::new("ssh").args(&args).output() {
                            Ok(output) => {
                                let exit_code = output.status.code().unwrap_or(1);
                                let combined = format!(
                                    "{}{}",
                                    String::from_utf8_lossy(&output.stdout),
                                    String::from_utf8_lossy(&output.stderr)
                                );
                                Ok((exit_code, remote::strip_ansi(&combined)))
                            }
                            Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                        }
                    } else {
                        Err(color_eyre::eyre::eyre!("No SSH connection"))
                    }
                } else {
                    match std::process::Command::new("bash")
                        .arg("-c")
                        .arg(prereq_script)
                        .output()
                    {
                        Ok(output) => {
                            let exit_code = output.status.code().unwrap_or(1);
                            let combined = format!(
                                "{}{}",
                                String::from_utf8_lossy(&output.stdout),
                                String::from_utf8_lossy(&output.stderr)
                            );
                            Ok((exit_code, remote::strip_ansi(&combined)))
                        }
                        Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                    }
                };

                match result {
                    Ok((0, output)) => {
                        for line in output.lines() {
                            let trimmed = line.trim();
                            if !trimmed.is_empty() {
                                let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                            }
                        }
                        for svc in services {
                            let _ = tx.send(InstallUpdate::ServiceStatus {
                                name: svc.clone(),
                                status: InstallStatus::Healthy,
                            });
                        }
                        let _ = tx.send(InstallUpdate::Log("✓ Prerequisites ready".into()));
                    }
                    Ok((code, output)) => {
                        any_failed = true;
                        for line in output.lines() {
                            let trimmed = line.trim();
                            if !trimmed.is_empty() {
                                let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                            }
                        }
                        for svc in services {
                            let _ = tx.send(InstallUpdate::ServiceStatus {
                                name: svc.clone(),
                                status: InstallStatus::Failed(format!("exit code {code}")),
                            });
                        }
                        let _ = tx.send(InstallUpdate::Log(format!(
                            "FAILED: Prerequisites (exit code {code})"
                        )));
                    }
                    Err(e) => {
                        any_failed = true;
                        for svc in services {
                            let _ = tx.send(InstallUpdate::ServiceStatus {
                                name: svc.clone(),
                                status: InstallStatus::Failed(e.to_string()),
                            });
                        }
                        let _ = tx.send(InstallUpdate::Log(format!(
                            "ERROR: Prerequisites: {e}"
                        )));
                    }
                }

                if any_failed {
                    break;
                }
                continue;
            } else if stage_services_to_deploy.len() == 1
                && stage_services_to_deploy.first().map(|s| s.as_str()) == Some("_docker_cleanup")
            {
                let _ = tx.send(InstallUpdate::Log(
                    "Checking for existing Docker containers...".into(),
                ));
                for svc in services {
                    let _ = tx.send(InstallUpdate::ServiceStatus {
                        name: svc.clone(),
                        status: InstallStatus::Deploying,
                    });
                }

                // Build cleanup script
                // 1. Always: stop any *-busibox compose projects that conflict
                // 2. If clean_install: also remove containers, volumes, networks
                let cleanup_script = if clean_install {
                    format!(r#"
                        set -e
                        echo "Clean install: removing all busibox Docker resources..."
                        # Find and stop ALL busibox compose projects
                        for project in $(docker compose ls --format '{{{{.Name}}}}' 2>/dev/null | grep -i busibox || true); do
                            echo "  Stopping project: $project"
                            docker compose -p "$project" down --remove-orphans 2>&1 || true
                        done
                        # Also clean up any orphaned busibox containers
                        ORPHANS=$(docker ps -a --filter "name=.*busibox.*\|.*-postgres\|.*-redis\|.*-minio\|.*-milvus\|.*-authz\|.*-agent\|.*-litellm" --format '{{{{.Names}}}}' 2>/dev/null | grep -E "(dev|staging|prod|demo)-" || true)
                        if [ -n "$ORPHANS" ]; then
                            echo "  Removing orphaned containers: $ORPHANS"
                            echo "$ORPHANS" | xargs docker rm -f 2>/dev/null || true
                        fi
                        # Remove busibox volumes (preserve model caches for faster reinstall)
                        VOLS=$(docker volume ls --format '{{{{.Name}}}}' 2>/dev/null | grep -E "(dev|staging|prod|demo)-busibox" | grep -v -E "model_cache|fastembed_cache|vllm_cache|ollama" || true)
                        if [ -n "$VOLS" ]; then
                            echo "  Removing volumes: $VOLS"
                            echo "$VOLS" | xargs docker volume rm -f 2>/dev/null || true
                        fi
                        PRESERVED=$(docker volume ls --format '{{{{.Name}}}}' 2>/dev/null | grep -E "(dev|staging|prod|demo)-busibox" | grep -E "model_cache|fastembed_cache|vllm_cache|ollama" || true)
                        if [ -n "$PRESERVED" ]; then
                            echo "  ✓ Preserved model cache volumes: $PRESERVED"
                        fi
                        # Remove busibox networks
                        NETS=$(docker network ls --format '{{{{.Name}}}}' 2>/dev/null | grep -E "(dev|staging|prod|demo)-busibox" || true)
                        if [ -n "$NETS" ]; then
                            echo "  Removing networks: $NETS"
                            echo "$NETS" | xargs docker network rm 2>/dev/null || true
                        fi
                        # Check for non-Docker processes on ports we need
                        BLOCKED_PORTS=""
                        for port in 5432 6379 9000 19530 8010 4111 8002 8001 3000; do
                            # Use lsof to find listeners (works on macOS and Linux)
                            HOLDER=$(lsof -i ":$port" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)
                            if [ -n "$HOLDER" ]; then
                                PNAME=$(ps -p "$HOLDER" -o comm= 2>/dev/null || echo "unknown")
                                # Skip if it's a docker process (those are expected)
                                if ! echo "$PNAME" | grep -qi "docker\|com.docker\|vpnkit"; then
                                    echo "  ⚠ Port $port in use by non-Docker process: $PNAME (PID $HOLDER)"
                                    # Try to stop common services
                                    case "$PNAME" in
                                        postgres|postmaster)
                                            echo "    → Stopping local PostgreSQL..."
                                            if [ "$(uname)" = "Darwin" ]; then
                                                brew services stop postgresql 2>/dev/null || true
                                                brew services stop postgresql@14 2>/dev/null || true
                                                brew services stop postgresql@15 2>/dev/null || true
                                                brew services stop postgresql@16 2>/dev/null || true
                                            else
                                                sudo systemctl stop postgresql 2>/dev/null || true
                                                sudo service postgresql stop 2>/dev/null || true
                                            fi
                                            sleep 1
                                            # Verify it stopped
                                            if lsof -i ":$port" -sTCP:LISTEN -t &>/dev/null; then
                                                BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            else
                                                echo "    ✓ PostgreSQL stopped"
                                            fi
                                            ;;
                                        redis-server|redis)
                                            echo "    → Stopping local Redis..."
                                            if [ "$(uname)" = "Darwin" ]; then
                                                brew services stop redis 2>/dev/null || true
                                            else
                                                sudo systemctl stop redis 2>/dev/null || sudo systemctl stop redis-server 2>/dev/null || true
                                            fi
                                            sleep 1
                                            if lsof -i ":$port" -sTCP:LISTEN -t &>/dev/null; then
                                                BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            else
                                                echo "    ✓ Redis stopped"
                                            fi
                                            ;;
                                        *)
                                            BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            ;;
                                    esac
                                fi
                            fi
                        done
                        if [ -n "$BLOCKED_PORTS" ]; then
                            echo "  ⚠ WARNING: Ports still in use:$BLOCKED_PORTS"
                            echo "  Some services may fail to start. Stop the processes manually if needed."
                        fi
                        echo "✓ Clean install: all previous busibox resources removed"
                    "#)
                } else {
                    format!(r#"
                        set -e
                        PREFIX="{vault_prefix}"
                        # Check for running busibox compose projects and stop conflicting ones
                        # Normal install: only stop containers, preserve volumes/images/caches for faster rebuilds
                        PROJECTS=$(docker compose ls --format '{{{{.Name}}}}' 2>/dev/null | grep -i busibox || true)
                        if [ -n "$PROJECTS" ]; then
                            echo "  Found compose projects: $PROJECTS"
                            for project in $PROJECTS; do
                                if [ "$project" = "${{PREFIX}}-busibox" ]; then
                                    echo "  Found current project: $project (will be updated)"
                                else
                                    echo "  Stopping conflicting project: $project"
                                    docker compose -p "$project" stop 2>&1 || true
                                fi
                            done
                        fi
                        # Check for port conflicts from other Docker containers
                        for port in 5432 6379 9000 19530 8010 4111 8002 8001 3000; do
                            HOLDER=$(docker ps --filter "publish=$port" --format '{{{{.Names}}}}' 2>/dev/null || true)
                            if [ -n "$HOLDER" ]; then
                                if ! echo "$HOLDER" | grep -q "^${{PREFIX}}-"; then
                                    echo "  ⚠ Port $port in use by: $HOLDER — stopping it"
                                    docker stop "$HOLDER" 2>/dev/null || true
                                fi
                            fi
                        done
                        # Check for non-Docker processes on critical ports
                        BLOCKED_PORTS=""
                        for port in 5432 6379 9000 19530 8010 4111 8002 8001 3000; do
                            HOLDER=$(lsof -i ":$port" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)
                            if [ -n "$HOLDER" ]; then
                                PNAME=$(ps -p "$HOLDER" -o comm= 2>/dev/null || echo "unknown")
                                if ! echo "$PNAME" | grep -qi "docker\|com.docker\|vpnkit"; then
                                    echo "  ⚠ Port $port in use by non-Docker process: $PNAME (PID $HOLDER)"
                                    case "$PNAME" in
                                        postgres|postmaster)
                                            echo "    → Stopping local PostgreSQL..."
                                            if [ "$(uname)" = "Darwin" ]; then
                                                brew services stop postgresql 2>/dev/null || true
                                                brew services stop postgresql@14 2>/dev/null || true
                                                brew services stop postgresql@15 2>/dev/null || true
                                                brew services stop postgresql@16 2>/dev/null || true
                                            else
                                                sudo systemctl stop postgresql 2>/dev/null || true
                                                sudo service postgresql stop 2>/dev/null || true
                                            fi
                                            sleep 1
                                            if lsof -i ":$port" -sTCP:LISTEN -t &>/dev/null; then
                                                BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            else
                                                echo "    ✓ PostgreSQL stopped"
                                            fi
                                            ;;
                                        redis-server|redis)
                                            echo "    → Stopping local Redis..."
                                            if [ "$(uname)" = "Darwin" ]; then
                                                brew services stop redis 2>/dev/null || true
                                            else
                                                sudo systemctl stop redis 2>/dev/null || sudo systemctl stop redis-server 2>/dev/null || true
                                            fi
                                            sleep 1
                                            if lsof -i ":$port" -sTCP:LISTEN -t &>/dev/null; then
                                                BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            else
                                                echo "    ✓ Redis stopped"
                                            fi
                                            ;;
                                        *)
                                            BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            ;;
                                    esac
                                fi
                            fi
                        done
                        if [ -n "$BLOCKED_PORTS" ]; then
                            echo "  ⚠ WARNING: Ports still in use:$BLOCKED_PORTS"
                            echo "  Some services may fail to start. Stop the processes manually if needed."
                        fi
                        echo "✓ Docker environment ready"
                    "#)
                };

                let result: color_eyre::Result<(i32, String)> = if is_remote {
                    if let Some((ref host, ref user, ref key)) = ssh_details {
                        let ssh = crate::modules::ssh::SshConnection::new(
                            profile_host.as_deref().unwrap_or(host),
                            user,
                            key,
                        );
                        let full_cmd = format!(
                            "bash -c {}",
                            shell_escape(&cleanup_script)
                        );
                        let mut args: Vec<String> = vec![
                            "-o".into(), "BatchMode=yes".into(),
                            "-o".into(), "StrictHostKeyChecking=accept-new".into(),
                            "-o".into(), "ConnectTimeout=10".into(),
                        ];
                        let key_expanded = crate::modules::ssh::shellexpand_path(key);
                        if !key_expanded.is_empty()
                            && std::path::Path::new(&key_expanded).exists()
                        {
                            args.push("-i".into());
                            args.push(key_expanded);
                        }
                        args.push(ssh.ssh_target());
                        args.push(full_cmd);
                        match std::process::Command::new("ssh").args(&args).output() {
                            Ok(output) => {
                                let exit_code = output.status.code().unwrap_or(1);
                                let combined = format!(
                                    "{}{}",
                                    String::from_utf8_lossy(&output.stdout),
                                    String::from_utf8_lossy(&output.stderr)
                                );
                                Ok((exit_code, remote::strip_ansi(&combined)))
                            }
                            Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                        }
                    } else {
                        Err(color_eyre::eyre::eyre!("No SSH details"))
                    }
                } else {
                    match std::process::Command::new("bash")
                        .arg("-c")
                        .arg(&cleanup_script)
                        .output()
                    {
                        Ok(output) => {
                            let exit_code = output.status.code().unwrap_or(1);
                            let combined = format!(
                                "{}{}",
                                String::from_utf8_lossy(&output.stdout),
                                String::from_utf8_lossy(&output.stderr)
                            );
                            Ok((exit_code, remote::strip_ansi(&combined)))
                        }
                        Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                    }
                };

                match result {
                    Ok((code, output)) => {
                        for line in output.lines() {
                            let trimmed = line.trim();
                            if !trimmed.is_empty() {
                                let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                            }
                        }
                        if code == 0 {
                            for svc in services {
                                let _ = tx.send(InstallUpdate::ServiceStatus {
                                    name: svc.clone(),
                                    status: InstallStatus::Healthy,
                                });
                            }
                            let _ = tx.send(InstallUpdate::Log(
                                "✓ Docker cleanup complete".into(),
                            ));
                        } else {
                            for svc in services {
                                let _ = tx.send(InstallUpdate::ServiceStatus {
                                    name: svc.clone(),
                                    status: InstallStatus::Failed(format!("exit code {code}")),
                                });
                            }
                            let _ = tx.send(InstallUpdate::Log(format!(
                                "WARNING: Docker cleanup had issues (exit code {code})"
                            )));
                            // Don't abort — cleanup failures are non-fatal
                        }
                    }
                    Err(e) => {
                        let _ = tx.send(InstallUpdate::Log(format!(
                            "WARNING: Docker cleanup skipped: {e}"
                        )));
                        for svc in services {
                            let _ = tx.send(InstallUpdate::ServiceStatus {
                                name: svc.clone(),
                                status: InstallStatus::Healthy, // Non-fatal, continue
                            });
                        }
                    }
                }
                continue;
            }

            let service_list = stage_services_to_deploy.join(",");
            let _ = tx.send(InstallUpdate::Log(format!(
                "Deploying {stage_name}: {service_list}..."
            )));

            for svc in services {
                let _ = tx.send(InstallUpdate::ServiceStatus {
                    name: svc.clone(),
                    status: InstallStatus::Deploying,
                });
            }

            let make_args = format!("install SERVICE={service_list}");

            // Use streaming functions so each line appears in the log immediately
            let tx_stream = tx.clone();
            let on_line = |line: &str| {
                let _ = tx_stream.send(InstallUpdate::Log(format!("  {line}")));
            };

            let result: color_eyre::Result<i32> = if is_remote {
                if let Some((ref host, ref user, ref key)) = ssh_details {
                    let ssh =
                        crate::modules::ssh::SshConnection::new(host, user, key);
                    if let Some(ref vp) = vault_password {
                        remote::exec_make_quiet_with_vault_streaming(
                            &ssh,
                            &remote_path,
                            &make_args,
                            vp,
                            on_line,
                        )
                    } else {
                        remote::exec_make_quiet_streaming(
                            &ssh,
                            &remote_path,
                            &make_args,
                            on_line,
                        )
                    }
                } else {
                    Err(color_eyre::eyre::eyre!("No SSH connection"))
                }
            } else if let Some(ref vp) = vault_password {
                remote::run_local_make_quiet_with_vault_streaming(
                    &repo_root,
                    &make_args,
                    vp,
                    on_line,
                )
            } else {
                remote::run_local_make_quiet_streaming(
                    &repo_root,
                    &make_args,
                    on_line,
                )
            };

            match result {
                Ok(0) => {
                    for svc in services {
                        let _ = tx.send(InstallUpdate::ServiceStatus {
                            name: svc.clone(),
                            status: InstallStatus::Healthy,
                        });
                    }
                    let _ = tx.send(InstallUpdate::Log(format!("✓ {stage_name} installed")));
                }
                Ok(code) => {
                    any_failed = true;
                    for svc in services {
                        let _ = tx.send(InstallUpdate::ServiceStatus {
                            name: svc.clone(),
                            status: InstallStatus::Failed(format!("exit code {code}")),
                        });
                    }
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "FAILED: {stage_name} (exit code {code})"
                    )));
                }
                Err(e) => {
                    any_failed = true;
                    for svc in services {
                        let _ = tx.send(InstallUpdate::ServiceStatus {
                            name: svc.clone(),
                            status: InstallStatus::Failed(e.to_string()),
                        });
                    }
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "ERROR: {stage_name}: {e}"
                    )));
                }
            }

            // Stop on first failure - don't continue to next stage
            if any_failed {
                break;
            }
        }

        if let Some(handle) = model_download_handle {
            let _ = tx.send(InstallUpdate::Log(
                "Waiting for model download to complete...".into(),
            ));
            match handle.join() {
                Ok(0) => {
                    let _ = tx.send(InstallUpdate::Log(
                        "✓ Models downloaded successfully".into(),
                    ));
                }
                _ => {
                    let _ = tx.send(InstallUpdate::Log(
                        "Models will download on first use".into(),
                    ));
                }
            }
        }

        if any_failed {
            let _ = tx.send(InstallUpdate::Log(
                "✗ Installation finished with errors — check logs for details".into(),
            ));
            let _ = tx.send(InstallUpdate::Complete {
                portal_url: None,
            });
        } else {
            let _ = tx.send(InstallUpdate::Log(
                "✓ Bootstrap installation complete".into(),
            ));
            let portal_url = if is_remote {
                let host = ssh_details
                    .as_ref()
                    .map(|(h, _, _)| h.as_str())
                    .unwrap_or("localhost");
                format!("http://{host}/portal/setup")
            } else {
                "http://localhost/portal/setup".to_string()
            };
            let _ = tx.send(InstallUpdate::Complete {
                portal_url: Some(portal_url),
            });
        }
    });
}

pub fn open_browser(url: &str) {
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open")
            .arg(url)
            .spawn();
    }
    #[cfg(target_os = "linux")]
    {
        let _ = std::process::Command::new("xdg-open")
            .arg(url)
            .spawn();
    }
}
