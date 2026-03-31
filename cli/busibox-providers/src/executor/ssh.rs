use super::{CommandOutput, Executor};
use busibox_core::shell::SHELL_PATH_PREAMBLE;
use busibox_core::ssh::SshConnection;
use color_eyre::Result;
use std::collections::HashMap;
use std::path::Path;

pub struct SshExecutor {
    pub host: String,
    pub user: String,
    pub key_path: String,
    pub remote_path: String,
}

impl SshExecutor {
    pub fn new(host: String, user: String, key_path: String, remote_path: String) -> Self {
        Self { host, user, key_path, remote_path }
    }

    fn connection(&self) -> SshConnection {
        SshConnection::new(&self.host, &self.user, &self.key_path)
    }
}

impl Executor for SshExecutor {
    fn run_command(&self, cmd: &str) -> Result<CommandOutput> {
        let full_cmd = format!("{SHELL_PATH_PREAMBLE}{cmd}");
        match self.connection().run(&full_cmd) {
            Ok(stdout) => Ok(CommandOutput {
                exit_code: 0,
                stdout,
                stderr: String::new(),
            }),
            Err(e) => Ok(CommandOutput {
                exit_code: 1,
                stdout: String::new(),
                stderr: e.to_string(),
            }),
        }
    }

    fn run_streaming(
        &self,
        cmd: &str,
        env: &HashMap<String, String>,
        tx: &std::sync::mpsc::Sender<String>,
    ) -> Result<i32> {
        use std::io::BufRead;
        use std::process::{Command, Stdio};

        let env_prefix: String = env
            .iter()
            .map(|(k, v)| format!("export {k}={v}; "))
            .collect();
        let full_cmd = format!("{SHELL_PATH_PREAMBLE}{env_prefix}{cmd}");

        let mut ssh_args = vec![
            "ssh".to_string(),
            "-o".to_string(), "BatchMode=yes".to_string(),
            "-o".to_string(), "StrictHostKeyChecking=accept-new".to_string(),
            "-o".to_string(), "ConnectTimeout=10".to_string(),
        ];
        let key = busibox_core::ssh::shellexpand_path(&self.key_path);
        if !key.is_empty() && std::path::Path::new(&key).exists() {
            ssh_args.push("-i".to_string());
            ssh_args.push(key);
        }
        ssh_args.push(format!("{}@{}", self.user, self.host));
        ssh_args.push(full_cmd);

        let mut child = Command::new(&ssh_args[0])
            .args(&ssh_args[1..])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;

        if let Some(stdout) = child.stdout.take() {
            let reader = std::io::BufReader::new(stdout);
            for line in reader.lines() {
                if let Ok(line) = line {
                    let _ = tx.send(line);
                }
            }
        }

        let status = child.wait()?;
        Ok(status.code().unwrap_or(-1))
    }

    fn run_make(
        &self,
        target: &str,
        env: &HashMap<String, String>,
        tx: &std::sync::mpsc::Sender<String>,
    ) -> Result<i32> {
        let cmd = format!("cd {} && USE_MANAGER=0 make {target} 2>&1", self.remote_path);
        self.run_streaming(&cmd, env, tx)
    }

    fn sync_repo(&self, local_path: &Path) -> Result<()> {
        use std::process::{Command, Stdio};

        let excludes: Vec<String> = [".git/", ".cursor/", ".vscode/", ".idea/"]
            .iter()
            .flat_map(|e| vec!["--exclude".to_string(), e.to_string()])
            .collect();

        let dest = format!("{}@{}:{}", self.user, self.host, self.remote_path);
        let mut args = vec![
            "rsync".to_string(),
            "-az".to_string(),
            "--delete".to_string(),
            "--include=ssl/".to_string(),
            "--include=ssl/**".to_string(),
            "--filter=:- .gitignore".to_string(),
        ];
        args.extend(excludes);
        let key = busibox_core::ssh::shellexpand_path(&self.key_path);
        if !key.is_empty() {
            args.push("-e".to_string());
            args.push(format!("ssh -i {key} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"));
        }
        args.push(format!("{}/", local_path.display()));
        args.push(dest);

        let status = Command::new(&args[0])
            .args(&args[1..])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()?;

        if status.success() {
            Ok(())
        } else {
            Err(color_eyre::eyre::eyre!("rsync failed with exit code {:?}", status.code()))
        }
    }
}
