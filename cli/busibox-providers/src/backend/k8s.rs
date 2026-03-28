use super::{Action, Backend, CheckResult, ServiceStatus};
use busibox_core::deploy::{DeployContext, DeploymentState};
use busibox_core::services;
use color_eyre::Result;
use std::collections::HashMap;
use std::sync::mpsc::Sender;

/// K8s backend — deploys via `make k8s-deploy` and manages via kubectl.
pub struct K8sBackend {
    pub ctx: DeployContext,
}

impl K8sBackend {
    pub fn new(ctx: DeployContext) -> Self {
        Self { ctx }
    }

    fn kubectl_env(&self) -> HashMap<String, String> {
        let mut env = HashMap::new();
        if let Some(ref kc) = self.ctx.kubeconfig {
            env.insert("KUBECONFIG".to_string(), kc.clone());
        }
        env
    }
}

impl Backend for K8sBackend {
    fn name(&self) -> &str {
        "k8s"
    }

    fn detect_installation(&self) -> Result<DeploymentState> {
        Ok(DeploymentState::Unknown)
    }

    fn deploy_service(
        &self,
        _service: &str,
        env: &HashMap<String, String>,
        tx: &Sender<String>,
    ) -> Result<i32> {
        use std::io::BufRead;
        use std::process::{Command, Stdio};

        let mut all_env = self.kubectl_env();
        all_env.extend(env.clone());
        if let Some(ref overlay) = self.ctx.k8s_overlay {
            all_env.insert("K8S_OVERLAY".to_string(), overlay.clone());
        }

        let cmd = format!(
            "cd {} && make k8s-deploy 2>&1",
            self.ctx.repo_root.display()
        );

        let mut command = Command::new("bash");
        command.arg("-c").arg(&cmd);
        for (k, v) in &all_env {
            command.env(k, v);
        }
        if let Some(ref vp) = self.ctx.vault_password {
            command.env("ANSIBLE_VAULT_PASSWORD", vp);
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
        let kube_env = self.kubectl_env();
        let deployment = services::container_for_service(service).unwrap_or(service);

        let cmd = match action {
            Action::Start => format!("kubectl scale deployment/{deployment} --replicas=1"),
            Action::Stop => format!("kubectl scale deployment/{deployment} --replicas=0"),
            Action::Restart => format!("kubectl rollout restart deployment/{deployment}"),
            Action::Logs => format!("kubectl logs deployment/{deployment} --tail=100"),
            Action::Status => format!("kubectl get pods -l app={deployment}"),
            Action::Redeploy => format!("kubectl rollout restart deployment/{deployment}"),
        };

        let mut command = std::process::Command::new("bash");
        command.arg("-c").arg(&cmd);
        for (k, v) in &kube_env {
            command.env(k, v);
        }
        command.status()?;
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
        let kubectl_ok = which::which("kubectl").is_ok();
        Ok(vec![CheckResult {
            name: "kubectl".to_string(),
            passed: kubectl_ok,
            message: if kubectl_ok {
                "kubectl is installed".to_string()
            } else {
                "kubectl is not installed".to_string()
            },
        }])
    }

    fn supported_services(&self) -> Vec<String> {
        let mut all = Vec::new();
        for group in services::group_order("k8s") {
            for svc in services::services_for_group(group, "k8s", false) {
                all.push(svc.to_string());
            }
        }
        all
    }
}
