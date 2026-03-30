use crate::app::{App, Screen};
use crate::modules::remote::{KeyState, LiveState, SecretKeyStatus};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

const SPINNER_FRAMES: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

fn key_state_label(state: &KeyState) -> (&str, Style) {
    match state {
        KeyState::Ok => ("OK", theme::success()),
        KeyState::Missing => ("MISSING", theme::error()),
        KeyState::Placeholder => ("PLACEHOLDER", theme::warning()),
        KeyState::InsecureDefault => ("INSECURE", theme::warning()),
        KeyState::NullOrEmpty => ("NULL/EMPTY", theme::error()),
        KeyState::NotChecked => ("n/a", theme::dim()),
        KeyState::Pending => ("...", theme::info()),
    }
}

fn live_state_label(state: &LiveState) -> (&str, Style) {
    match state {
        LiveState::NotChecked => ("n/a", theme::dim()),
        LiveState::Pending => ("...", theme::info()),
        LiveState::Pass => ("PASS", theme::success()),
        LiveState::Fail(_) => ("FAIL", theme::error()),
        LiveState::EnvMatch => ("MATCH", theme::success()),
        LiveState::EnvMismatch => ("MISMATCH", theme::error()),
        LiveState::Skipped => ("skip", theme::dim()),
    }
}

fn live_state_icon(state: &LiveState) -> &'static str {
    match state {
        LiveState::Pass | LiveState::EnvMatch => "✓ ",
        LiveState::Fail(_) | LiveState::EnvMismatch => "✗ ",
        _ => "  ",
    }
}

pub fn render(f: &mut Frame, app: &App) {
    let area = f.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1), // profile header spacer
            Constraint::Length(3), // title
            Constraint::Length(3), // info block
            Constraint::Min(8),   // table
            Constraint::Length(2), // help bar
        ])
        .margin(1)
        .split(area);

    // Title
    let title = Paragraph::new("Validate Secrets")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[1]);

    // Info block: vault file, status summary
    let info_text = if app.validate_secrets_loading {
        let tick = app.manage_tick;
        let frame = SPINNER_FRAMES[tick % SPINNER_FRAMES.len()];
        format!("{} Loading vault secrets and running live checks...", frame)
    } else if let Some(ref err) = app.validate_secrets_error {
        format!("Error: {}", err)
    } else {
        let total = app.validate_secrets_results.len();
        let required: Vec<&SecretKeyStatus> = app
            .validate_secrets_results
            .iter()
            .filter(|k| k.required)
            .collect();
        let ok_count = required.iter().filter(|k| k.local == KeyState::Ok).count();
        let live_pass = required
            .iter()
            .filter(|k| matches!(k.live, LiveState::Pass | LiveState::EnvMatch))
            .count();
        let live_fail = required
            .iter()
            .filter(|k| k.live.is_bad())
            .count();
        let remote_label = if app.validate_secrets_is_remote {
            let remote_ok = required
                .iter()
                .filter(|k| k.remote == KeyState::Ok)
                .count();
            format!(" | Remote: {}/{} OK", remote_ok, required.len())
        } else {
            String::new()
        };
        let live_label = if live_fail > 0 {
            format!(" | Live: {}/{} OK, {} FAIL", live_pass, required.len(), live_fail)
        } else {
            format!(" | Live: {}/{} OK", live_pass, required.len())
        };
        format!(
            "Vault: {}  |  Local: {}/{} OK  |  {} keys{}{}",
            app.validate_secrets_vault_file,
            ok_count,
            required.len(),
            total,
            live_label,
            remote_label,
        )
    };

    let info_style = if app.validate_secrets_error.is_some() {
        theme::error()
    } else if app.validate_secrets_loading {
        theme::info()
    } else {
        theme::normal()
    };
    let info = Paragraph::new(info_text)
        .style(info_style)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim()),
        );
    f.render_widget(info, chunks[2]);

    // Table
    let table_area = chunks[3];

    if app.validate_secrets_loading && app.validate_secrets_results.is_empty() {
        let loading = Paragraph::new("Decrypting vault and checking services...")
            .style(theme::info())
            .alignment(Alignment::Center)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim())
                    .title(" Secrets "),
            );
        f.render_widget(loading, table_area);
    } else if app.validate_secrets_results.is_empty() {
        let empty = Paragraph::new("No secrets found")
            .style(theme::muted())
            .alignment(Alignment::Center)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim())
                    .title(" Secrets "),
            );
        f.render_widget(empty, table_area);
    } else {
        render_secrets_table(f, app, table_area);
    }

    // Help bar
    let help = Paragraph::new(Line::from(vec![
        Span::styled(" Esc ", theme::highlight()),
        Span::styled("Back  ", theme::normal()),
        Span::styled("r ", theme::highlight()),
        Span::styled("Refresh  ", theme::normal()),
        Span::styled("j/k ", theme::muted()),
        Span::styled("Scroll", theme::muted()),
    ]));
    f.render_widget(help, chunks[4]);
}

fn render_secrets_table(f: &mut Frame, app: &App, area: Rect) {
    let show_remote = app.validate_secrets_is_remote;

    // Build header -- always show Live column
    let mut header_cells = vec![
        Cell::from(" Key").style(theme::heading()),
        Cell::from("Required").style(theme::heading()),
        Cell::from("Local").style(theme::heading()),
        Cell::from("Live").style(theme::heading()),
    ];
    if show_remote {
        header_cells.push(Cell::from("Remote").style(theme::heading()));
    }
    let header = Row::new(header_cells)
        .style(Style::default().bg(theme::BRAND_DIM))
        .height(1);

    // Build rows
    let results = &app.validate_secrets_results;
    let visible_height = area.height.saturating_sub(4) as usize;
    let scroll = app.validate_secrets_scroll.min(results.len().saturating_sub(visible_height));

    let rows: Vec<Row> = results
        .iter()
        .skip(scroll)
        .take(visible_height)
        .map(|entry| {
            let (local_label, local_style) = key_state_label(&entry.local);
            let local_icon = match &entry.local {
                KeyState::Ok => "✓ ",
                KeyState::NotChecked | KeyState::Pending => "  ",
                _ => "✗ ",
            };

            let (live_label, live_style) = live_state_label(&entry.live);
            let live_icon = live_state_icon(&entry.live);

            let req_label = if entry.required { "yes" } else { "" };
            let req_style = if entry.required {
                theme::info()
            } else {
                theme::dim()
            };

            let key_style = if entry.required && (entry.local.is_bad() || entry.live.is_bad()) {
                theme::error()
            } else if !entry.required {
                theme::muted()
            } else {
                theme::normal()
            };

            let mut cells = vec![
                Cell::from(format!(" {}", entry.key_path)).style(key_style),
                Cell::from(req_label).style(req_style),
                Cell::from(format!("{}{}", local_icon, local_label)).style(local_style),
                Cell::from(format!("{}{}", live_icon, live_label)).style(live_style),
            ];

            if show_remote {
                let (remote_label, remote_style) = key_state_label(&entry.remote);
                let remote_icon = match &entry.remote {
                    KeyState::Ok => "✓ ",
                    KeyState::NotChecked | KeyState::Pending => "  ",
                    _ => "✗ ",
                };
                cells.push(
                    Cell::from(format!("{}{}", remote_icon, remote_label)).style(remote_style),
                );
            }

            Row::new(cells)
        })
        .collect();

    let mut widths = vec![
        Constraint::Min(28),
        Constraint::Length(10),
        Constraint::Length(16),
        Constraint::Length(16),
    ];
    if show_remote {
        widths.push(Constraint::Length(16));
    }

    let table = Table::new(rows, widths)
        .header(header)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Secrets "),
        );
    f.render_widget(table, area);

    // Scrollbar
    if results.len() > visible_height {
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(None)
            .end_symbol(None);
        let mut scrollbar_state =
            ScrollbarState::new(results.len().saturating_sub(visible_height)).position(scroll);
        f.render_stateful_widget(
            scrollbar,
            area.inner(Margin {
                vertical: 1,
                horizontal: 0,
            }),
            &mut scrollbar_state,
        );
    }
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.validate_secrets_results.clear();
            app.validate_secrets_scroll = 0;
            app.validate_secrets_loading = false;
            app.validate_secrets_error = None;
        }
        KeyCode::Char('r') if !app.validate_secrets_loading => {
            app.validate_secrets_results.clear();
            app.validate_secrets_scroll = 0;
            app.validate_secrets_error = None;
            app.pending_compare_secrets = true;
        }
        KeyCode::Down | KeyCode::Char('j') => {
            let max = app.validate_secrets_results.len().saturating_sub(1);
            if app.validate_secrets_scroll < max {
                app.validate_secrets_scroll += 1;
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.validate_secrets_scroll > 0 {
                app.validate_secrets_scroll -= 1;
            }
        }
        KeyCode::PageDown => {
            app.validate_secrets_scroll = (app.validate_secrets_scroll + 10)
                .min(app.validate_secrets_results.len().saturating_sub(1));
        }
        KeyCode::PageUp => {
            app.validate_secrets_scroll = app.validate_secrets_scroll.saturating_sub(10);
        }
        _ => {}
    }
}
