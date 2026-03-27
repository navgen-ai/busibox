use crate::app::{App, Screen};
use crate::modules::remote::{KeyState, SecretKeyStatus};
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
        let tick = app.manage_tick; // reuse manage_tick for spinner
        let frame = SPINNER_FRAMES[tick % SPINNER_FRAMES.len()];
        format!("{} Loading vault secrets...", frame)
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
        let remote_label = if app.validate_secrets_is_remote {
            let remote_ok = required
                .iter()
                .filter(|k| k.remote == KeyState::Ok)
                .count();
            format!(" | Remote: {}/{} OK", remote_ok, required.len())
        } else {
            String::new()
        };
        format!(
            "Vault: {}  |  Local: {}/{} required OK  |  {} total keys{}",
            app.validate_secrets_vault_file,
            ok_count,
            required.len(),
            total,
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
        let loading = Paragraph::new("Decrypting and parsing vault files...")
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

    // Build header
    let header_cells = if show_remote {
        vec![
            Cell::from(" Key").style(theme::heading()),
            Cell::from("Required").style(theme::heading()),
            Cell::from("Local").style(theme::heading()),
            Cell::from("Remote").style(theme::heading()),
        ]
    } else {
        vec![
            Cell::from(" Key").style(theme::heading()),
            Cell::from("Required").style(theme::heading()),
            Cell::from("Local").style(theme::heading()),
        ]
    };
    let header = Row::new(header_cells)
        .style(Style::default().bg(theme::BRAND_DIM))
        .height(1);

    // Build rows
    let results = &app.validate_secrets_results;
    let visible_height = area.height.saturating_sub(4) as usize; // borders + header
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

            let req_label = if entry.required { "yes" } else { "" };
            let req_style = if entry.required {
                theme::info()
            } else {
                theme::dim()
            };

            let key_style = if entry.required && entry.local.is_bad() {
                theme::error()
            } else if !entry.required {
                theme::muted()
            } else {
                theme::normal()
            };

            if show_remote {
                let (remote_label, remote_style) = key_state_label(&entry.remote);
                let remote_icon = match &entry.remote {
                    KeyState::Ok => "✓ ",
                    KeyState::NotChecked | KeyState::Pending => "  ",
                    _ => "✗ ",
                };
                Row::new(vec![
                    Cell::from(format!(" {}", entry.key_path)).style(key_style),
                    Cell::from(req_label).style(req_style),
                    Cell::from(format!("{}{}", local_icon, local_label)).style(local_style),
                    Cell::from(format!("{}{}", remote_icon, remote_label)).style(remote_style),
                ])
            } else {
                Row::new(vec![
                    Cell::from(format!(" {}", entry.key_path)).style(key_style),
                    Cell::from(req_label).style(req_style),
                    Cell::from(format!("{}{}", local_icon, local_label)).style(local_style),
                ])
            }
        })
        .collect();

    let widths = if show_remote {
        vec![
            Constraint::Min(28),
            Constraint::Length(10),
            Constraint::Length(16),
            Constraint::Length(16),
        ]
    } else {
        vec![
            Constraint::Min(28),
            Constraint::Length(10),
            Constraint::Length(16),
        ]
    };

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
