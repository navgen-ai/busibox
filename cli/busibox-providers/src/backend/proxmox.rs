use super::{Action, Backend, CheckResult, ServiceStatus};
use busibox_core::deploy::{DeployContext, DeploymentState};
use busibox_core::services;
use color_eyre::Result;
use std::collections::HashMap;
use std::sync::mpsc::Sender;

/// Proxmox backend — deploys services via `ansible-playbook site.yml` over SSH to LXC hosts.
pub struct ProxmoxBackend {
    pub ctx: DeployContext,
}

impl ProxmoxBackend {
    pub fn new(ctx: DeployContext) -> Self {
        Self { ctx }
    }

    fn inventory_path(&self) -> &str {
        match self.ctx.environment.as_str() {
            "production" => "inventory/production",
            "staging" => "inventory/staging",
            _ => "inventory/staging",
        }
    }

    fn ansible_cmd(&self, make_target: &str) -> String {
        let vault_prefix = &self.ctx.vault_prefix;
        let environment = &self.ctx.environment;
        let inv = self.inventory_path();

        let vault_flag = if self.ctx.vault_password.is_some() {
            "VAULT_FLAGS='--vault-password-file scripts/lib/vault-pass-from-env.sh'"
        } else {
            ""
        };

        format!(
            "cd {repo}/provision/ansible && {vault_flag} make {make_target} \
             INV={inv} DEPLOY_ENV={environment} VAULT_PREFIX={vault_prefix} 2>&1",
            repo = self.ctx.repo_root.display(),
        )
    }
}

impl Backend for ProxmoxBackend {
    fn name(&self) -> &str {
        "proxmox"
    }

    fn detect_installation(&self) -> Result<DeploymentState> {
        Ok(DeploymentState::Unknown)
    }

    fn deploy_service(
        &self,
        service: &str,
        env: &HashMap<String, String>,
        tx: &Sender<String>,
    ) -> Result<i32> {
        let target = services::proxmox_make_target(service);
        let cmd = self.ansible_cmd(target);

        use std::io::BufRead;
        use std::process::{Command, Stdio};

        let mut command = Command::new("bash");
        command.arg("-c").arg(&cmd);
        if let Some(ref vp) = self.ctx.vault_password {
            command.env("ANSIBLE_VAULT_PASSWORD", vp);
        }
        for (k, v) in env {
            command.env(k, v);
        }
        command.stdout(Stdio::piped()).stderr(Stdio::piped());

        let mut child = command.spawn()?;
        if let Some(stdout) = child.stdout.take() {
            for line in std::io::BufReader::new(stdout).lines().map_while(Result::ok) {
                let _ = tx.send(line);
            }
        }
        let status = child.wait()?;
        Ok(status.code().unwrap_or(-1))
    }

    fn service_action(
        &self,
        service: &str,
        action: &Action,
        _env: &HashMap<String, String>,
    ) -> Result<()> {
        let ssh = self.ctx.remote_host.as_ref()
            .ok_or_else(|| color_eyre::eyre::eyre!("Proxmox requires a remote host"))?;
        let user = self.ctx.remote_user.as_deref().unwrap_or("root");
        let key = self.ctx.remote_key.as_deref().unwrap_or("~/.ssh/id_ed25519");

        let conn = busibox_core::ssh::SshConnection::new(ssh, user, key);
        let service_name = services::container_for_service(service).unwrap_or(service);

        let cmd = match action {
            Action::Start => format!("systemctl start {service_name}"),
            Action::Stop => format!("systemctl stop {service_name}"),
            Action::Restart => format!("systemctl restart {service_name}"),
            Action::Logs => format!("journalctl -u {service_name} -n 100 --no-pager"),
            Action::Status => format!("systemctl status {service_name}"),
            Action::Redeploy => {
                let target = services::proxmox_make_target(service);
                return {
                    let cmd = self.ansible_cmd(target);
                    let mut command = std::process::Command::new("bash");
                    command.arg("-c").arg(&cmd);
                    if let Some(ref vp) = self.ctx.vault_password {
                        command.env("ANSIBLE_VAULT_PASSWORD", vp);
                    }
                    command.status()?;
                    Ok(())
                };
            }
        };

        conn.run(&cmd)?;
        Ok(())
    }

    fn get_service_status(&self, _service: &str) -> Result<ServiceStatus> {
        Ok(ServiceStatus::Unknown)
    }

    fn start_all(&self) -> Result<()> {
        Ok(())
    }

    fn stop_all(&self) -> Result<()> {
        Ok(())
    }

    fn prerequisite_checks(&self) -> Result<Vec<CheckResult>> {
        let ansible_ok = which::which("ansible-playbook").is_ok();
        Ok(vec![CheckResult {
            name: "ansible".to_string(),
            passed: ansible_ok,
            message: if ansible_ok {
                "Ansible is installed".to_string()
            } else {
                "Ansible is not installed".to_string()
            },
        }])
    }

    fn supported_services(&self) -> Vec<String> {
        let is_mlx = self.ctx.is_mlx();
        let mut all = Vec::new();
        for group in services::group_order("proxmox") {
            for svc in services::services_for_group(group, "proxmox", is_mlx) {
                all.push(svc.to_string());
            }
        }
        all
    }
}
