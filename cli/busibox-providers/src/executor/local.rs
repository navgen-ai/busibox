use super::{CommandOutput, Executor};
use color_eyre::Result;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub struct LocalExecutor {
    pub repo_root: PathBuf,
}

impl LocalExecutor {
    pub fn new(repo_root: PathBuf) -> Self {
        Self { repo_root }
    }
}

impl Executor for LocalExecutor {
    fn run_command(&self, cmd: &str) -> Result<CommandOutput> {
        let output = std::process::Command::new("bash")
            .arg("-c")
            .arg(cmd)
            .current_dir(&self.repo_root)
            .output()?;

        Ok(CommandOutput {
            exit_code: output.status.code().unwrap_or(-1),
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        })
    }

    fn run_streaming(
        &self,
        cmd: &str,
        env: &HashMap<String, String>,
        tx: &std::sync::mpsc::Sender<String>,
    ) -> Result<i32> {
        use std::io::BufRead;
        use std::process::{Command, Stdio};

        let mut command = Command::new("bash");
        command
            .arg("-c")
            .arg(cmd)
            .current_dir(&self.repo_root)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        for (k, v) in env {
            command.env(k, v);
        }

        let mut child = command.spawn()?;

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
        let cmd = format!("cd {} && USE_MANAGER=0 make {target} 2>&1", self.repo_root.display());
        self.run_streaming(&cmd, env, tx)
    }

    fn sync_repo(&self, _local_path: &Path) -> Result<()> {
        Ok(())
    }
}
