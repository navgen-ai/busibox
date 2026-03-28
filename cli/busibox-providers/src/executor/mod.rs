pub mod local;
pub mod ssh;

use color_eyre::Result;
use std::collections::HashMap;
use std::path::Path;

/// Abstraction over where commands run — locally or over SSH.
///
/// This replaces the parallel `run_local_make_*` / `exec_make_*` functions in
/// remote.rs with a single trait that both local and SSH executors implement.
pub trait Executor: Send + Sync {
    fn run_command(&self, cmd: &str) -> Result<CommandOutput>;

    fn run_streaming(
        &self,
        cmd: &str,
        env: &HashMap<String, String>,
        tx: &std::sync::mpsc::Sender<String>,
    ) -> Result<i32>;

    fn run_make(
        &self,
        target: &str,
        env: &HashMap<String, String>,
        tx: &std::sync::mpsc::Sender<String>,
    ) -> Result<i32>;

    fn sync_repo(&self, local_path: &Path) -> Result<()>;
}

#[derive(Debug, Clone)]
pub struct CommandOutput {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}
