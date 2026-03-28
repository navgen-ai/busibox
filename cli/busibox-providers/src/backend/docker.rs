use super::{Action, Backend, CheckResult, ServiceStatus};
use busibox_core::deploy::{DeployContext, DeploymentState};
use busibox_core::services;
use color_eyre::Result;
use std::collections::HashMap;
use std::sync::mpsc::Sender;

/// Docker backend — deploys services via `ansible-playbook docker.yml`.
pub struct DockerBackend {
    pub ctx: DeployContext,
}

impl DockerBackend {
    pub fn new(ctx: DeployContext) -> Self {
        Self { ctx }
    }

    fn ansible_cmd(&self, tag: &str, extra_vars: &HashMap<String, String>) -> String {
        let vault_prefix = &self.ctx.vault_prefix;
        let container_prefix = &self.ctx.container_prefix;
        let environment = &self.ctx.environment;

        let mut extra: Vec<String> = vec![
            format!("vault_prefix={vault_prefix}"),
            format!("container_prefix={container_prefix}"),
            format!("deployment_environment={environment}"),
            "docker_force_recreate=true".to_string(),
        ];
        for (k, v) in extra_vars {
            extra.push(format!("{k}={v}"));
        }
        let extra_str = extra.join(" -e ");

        let vault_flag = if self.ctx.vault_password.is_some() {
            "--vault-password-file scripts/lib/vault-pass-from-env.sh".to_string()
        } else {
            String::new()
        };

        format!(
            "cd {repo} && ansible-playbook -i provision/ansible/inventory/docker \
             provision/ansible/docker.yml --tags {tag} \
             -e {extra_str} {vault_flag} 2>&1",
            repo = self.ctx.repo_root.display(),
        )
    }
}

impl Backend for DockerBackend {
    fn name(&self) -> &str {
        "docker"
    }

    fn detect_installation(&self) -> Result<DeploymentState> {
        let prefix = &self.ctx.container_prefix;
        let output = std::process::Command::new("bash")
            .arg("-c")
            .arg(format!(
                "docker ps --filter name=^{prefix}- --format '{{{{.Names}}}}' 2>/dev/null | wc -l"
            ))
            .output();

        match output {
            Ok(o) if o.status.success() => {
                let count: usize = String::from_utf8_lossy(&o.stdout)
                    .trim()
                    .parse()
                    .unwrap_or(0);
                if count == 0 {
                    Ok(DeploymentState::None)
                } else {
                    Ok(DeploymentState::Partial(count))
                }
            }
            _ => Ok(DeploymentState::Unknown),
        }
    }

    fn deploy_service(
        &self,
        service: &str,
        env: &HashMap<String, String>,
        tx: &Sender<String>,
    ) -> Result<i32> {
        let tag = services::ansible_tag(service);
        let cmd = self.ansible_cmd(tag, env);

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
        env: &HashMap<String, String>,
    ) -> Result<()> {
        let container = services::container_for_service(service)
            .unwrap_or(service);
        let prefix = &self.ctx.container_prefix;
        let full_name = format!("{prefix}-{container}");

        let cmd = match action {
            Action::Start => format!("docker start {full_name}"),
            Action::Stop => format!("docker stop {full_name}"),
            Action::Restart => format!("docker restart {full_name}"),
            Action::Logs => format!("docker logs -f --tail 100 {full_name}"),
            Action::Status => format!("docker ps --filter name=^{full_name}$ --format '{{{{.Status}}}}'"),
            Action::Redeploy => {
                let tag = services::ansible_tag(service);
                self.ansible_cmd(tag, env)
            }
        };

        let mut command = std::process::Command::new("bash");
        command.arg("-c").arg(&cmd);
        if let Some(ref vp) = self.ctx.vault_password {
            command.env("ANSIBLE_VAULT_PASSWORD", vp);
        }
        for (k, v) in env {
            command.env(k, v);
        }

        let status = command.status()?;
        if !status.success() {
            return Err(color_eyre::eyre::eyre!(
                "Action {:?} on {} failed with exit code {:?}",
                action, service, status.code()
            ));
        }
        Ok(())
    }

    fn get_service_status(&self, service: &str) -> Result<ServiceStatus> {
        let container = services::container_for_service(service).unwrap_or(service);
        let prefix = &self.ctx.container_prefix;
        let full_name = format!("{prefix}-{container}");

        let output = std::process::Command::new("bash")
            .arg("-c")
            .arg(format!(
                "docker ps --filter name=^{full_name}$ --filter status=running --format '{{{{.Names}}}}' 2>/dev/null"
            ))
            .output();

        match output {
            Ok(o) if o.status.success() => {
                let out = String::from_utf8_lossy(&o.stdout).trim().to_string();
                if out.is_empty() {
                    Ok(ServiceStatus::Stopped)
                } else {
                    Ok(ServiceStatus::Running)
                }
            }
            _ => Ok(ServiceStatus::Unknown),
        }
    }

    fn start_all(&self) -> Result<()> {
        let cmd = self.ansible_cmd("all", &HashMap::new());
        let mut command = std::process::Command::new("bash");
        command.arg("-c").arg(&cmd);
        if let Some(ref vp) = self.ctx.vault_password {
            command.env("ANSIBLE_VAULT_PASSWORD", vp);
        }
        let status = command.status()?;
        if !status.success() {
            return Err(color_eyre::eyre::eyre!(
                "start_all failed with exit code {:?}",
                status.code()
            ));
        }
        Ok(())
    }

    fn stop_all(&self) -> Result<()> {
        let prefix = &self.ctx.container_prefix;
        let compose_project = format!("{prefix}-busibox");
        let mut cmd = std::process::Command::new("bash");
        cmd.arg("-c")
            .arg(format!(
                "cd {} && COMPOSE_PROJECT_NAME={} docker compose -f docker-compose.yml down 2>&1",
                self.ctx.repo_root.display(),
                compose_project,
            ));
        let status = cmd.status()?;
        if !status.success() {
            return Err(color_eyre::eyre::eyre!(
                "stop_all failed with exit code {:?}",
                status.code()
            ));
        }
        Ok(())
    }

    fn prerequisite_checks(&self) -> Result<Vec<CheckResult>> {
        let mut results = Vec::new();

        results.push(CheckResult {
            name: "docker".to_string(),
            passed: which::which("docker").is_ok(),
            message: if which::which("docker").is_ok() {
                "Docker is installed".to_string()
            } else {
                "Docker is not installed".to_string()
            },
        });

        let compose_ok = std::process::Command::new("docker")
            .args(["compose", "version"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);

        results.push(CheckResult {
            name: "docker-compose".to_string(),
            passed: compose_ok,
            message: if compose_ok {
                "Docker Compose is available".to_string()
            } else {
                "Docker Compose is not available".to_string()
            },
        });

        let ansible_ok = which::which("ansible-playbook").is_ok();
        results.push(CheckResult {
            name: "ansible".to_string(),
            passed: ansible_ok,
            message: if ansible_ok {
                "Ansible is installed".to_string()
            } else {
                "Ansible is not installed".to_string()
            },
        });

        Ok(results)
    }

    fn supported_services(&self) -> Vec<String> {
        let is_mlx = self.ctx.is_mlx();
        let mut all = Vec::new();
        for group in services::group_order("docker") {
            for svc in services::services_for_group(group, "docker", is_mlx) {
                all.push(svc.to_string());
            }
        }
        all
    }
}
