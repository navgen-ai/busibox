use crate::app::{App, Screen};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

const FIELD_KUBECONFIG: usize = 0;
const FIELD_OVERLAY: usize = 1;
const FIELD_SPOT_TOKEN: usize = 2;
const FIELD_ENV: usize = 3;
const FIELD_COUNT: usize = 4;

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Min(3),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Kubernetes Setup")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let subtitle = Paragraph::new("Configure Rackspace Spot K8s deployment")
        .style(theme::muted())
        .alignment(Alignment::Center);
    f.render_widget(subtitle, chunks[1]);

    render_text_field(
        f,
        chunks[2],
        " Kubeconfig Path ",
        &app.k8s_kubeconfig_input,
        "k8s/kubeconfig-rackspace-spot.yaml",
        app.k8s_input_cursor == FIELD_KUBECONFIG,
    );

    render_text_field(
        f,
        chunks[3],
        " K8s Overlay ",
        &app.k8s_overlay_input,
        "rackspace-spot",
        app.k8s_input_cursor == FIELD_OVERLAY,
    );

    let masked_token = if app.k8s_spot_token_input.is_empty() {
        String::new()
    } else if app.k8s_input_cursor == FIELD_SPOT_TOKEN {
        app.k8s_spot_token_input.clone()
    } else {
        let len = app.k8s_spot_token_input.len();
        if len <= 8 {
            "*".repeat(len)
        } else {
            format!(
                "{}...{}",
                &app.k8s_spot_token_input[..4],
                &app.k8s_spot_token_input[len - 4..]
            )
        }
    };

    render_text_field(
        f,
        chunks[4],
        " Spot API Token (optional) ",
        &masked_token,
        "(for spot instance management)",
        app.k8s_input_cursor == FIELD_SPOT_TOKEN,
    );

    let envs = app.env_choices();
    let env_text = envs
        .get(app.k8s_env_choice)
        .unwrap_or(&"staging");
    let env_style = if app.k8s_input_cursor == FIELD_ENV {
        theme::highlight()
    } else {
        theme::dim()
    };
    let env = Paragraph::new(format!("< {env_text} >"))
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(env_style)
                .title(" Environment ")
                .title_style(theme::heading()),
        )
        .style(theme::normal());
    f.render_widget(env, chunks[5]);

    let info_lines = vec![
        Line::from(Span::styled(
            "  The kubeconfig file connects the CLI to your K8s cluster.",
            theme::muted(),
        )),
        Line::from(Span::styled(
            "  The overlay selects which kustomize configuration to apply.",
            theme::muted(),
        )),
        Line::from(Span::styled(
            "  The Spot token enables node class swaps and bid management.",
            theme::muted(),
        )),
    ];
    let info = Paragraph::new(info_lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Info ")
            .title_style(theme::heading()),
    );
    f.render_widget(info, chunks[6]);

    let help = Paragraph::new(Line::from(vec![
        Span::styled("Tab ", theme::highlight()),
        Span::styled("Next field  ", theme::normal()),
        Span::styled("←/→ ", theme::highlight()),
        Span::styled("Cycle env  ", theme::normal()),
        Span::styled("Enter ", theme::highlight()),
        Span::styled("Continue  ", theme::normal()),
        Span::styled("Esc ", theme::muted()),
        Span::styled("Back", theme::muted()),
    ]));
    f.render_widget(help, chunks[7]);
}

fn render_text_field(
    f: &mut Frame,
    area: Rect,
    title: &str,
    value: &str,
    placeholder: &str,
    is_focused: bool,
) {
    let border_style = if is_focused {
        theme::highlight()
    } else {
        theme::dim()
    };

    let content = if is_focused {
        let display = if value.is_empty() {
            "▎".to_string()
        } else {
            format!("{value}▎")
        };
        Line::from(Span::styled(display, theme::normal()))
    } else {
        let display = if value.is_empty() {
            placeholder
        } else {
            value
        };
        Line::from(Span::styled(display, theme::muted()))
    };

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(border_style)
        .title(title)
        .title_style(theme::heading());

    let paragraph = Paragraph::new(content).block(block);
    f.render_widget(paragraph, area);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::SetupMode;
            app.k8s_input_cursor = 0;
        }
        KeyCode::Tab | KeyCode::Down => {
            app.k8s_input_cursor = (app.k8s_input_cursor + 1) % FIELD_COUNT;
        }
        KeyCode::BackTab | KeyCode::Up => {
            if app.k8s_input_cursor > 0 {
                app.k8s_input_cursor -= 1;
            } else {
                app.k8s_input_cursor = FIELD_COUNT - 1;
            }
        }
        KeyCode::Left => {
            if app.k8s_input_cursor == FIELD_ENV && app.k8s_env_choice > 0 {
                app.k8s_env_choice -= 1;
            }
        }
        KeyCode::Right => {
            if app.k8s_input_cursor == FIELD_ENV {
                let max = app.env_choices().len() - 1;
                if app.k8s_env_choice < max {
                    app.k8s_env_choice += 1;
                }
            }
        }
        KeyCode::Enter => {
            save_and_continue(app);
        }
        KeyCode::Char(c) => match app.k8s_input_cursor {
            FIELD_KUBECONFIG => app.k8s_kubeconfig_input.push(c),
            FIELD_OVERLAY => app.k8s_overlay_input.push(c),
            FIELD_SPOT_TOKEN => app.k8s_spot_token_input.push(c),
            _ => {}
        },
        KeyCode::Backspace => match app.k8s_input_cursor {
            FIELD_KUBECONFIG => {
                app.k8s_kubeconfig_input.pop();
            }
            FIELD_OVERLAY => {
                app.k8s_overlay_input.pop();
            }
            FIELD_SPOT_TOKEN => {
                app.k8s_spot_token_input.pop();
            }
            _ => {}
        },
        _ => {}
    }
}

fn save_and_continue(app: &mut App) {
    let kubeconfig = if app.k8s_kubeconfig_input.trim().is_empty() {
        let default_path = app
            .repo_root
            .join("k8s/kubeconfig-rackspace-spot.yaml")
            .display()
            .to_string();
        app.k8s_kubeconfig_input = default_path.clone();
        default_path
    } else {
        app.k8s_kubeconfig_input.trim().to_string()
    };

    if app.k8s_overlay_input.trim().is_empty() {
        app.k8s_overlay_input = "rackspace-spot".into();
    }

    let k8s_env_choice = app.k8s_env_choice;
    app.remote_env_choice = k8s_env_choice;

    let k8s_backend_idx = app
        .backend_choices()
        .iter()
        .position(|b| b.contains("K8s"))
        .unwrap_or(2);
    app.remote_backend_choice = k8s_backend_idx;

    let overlay_name = app.k8s_overlay_input.trim().to_string();
    app.remote_host_input = format!("k8s-{overlay_name}");

    let _ = kubeconfig; // used indirectly through app state
    app.setup_target = crate::app::SetupTarget::Remote;
    app.screen = Screen::HardwareReport;
}
