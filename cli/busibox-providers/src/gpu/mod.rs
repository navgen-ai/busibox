pub mod cloud;
pub mod cuda;
pub mod mlx;

use busibox_core::hardware::GpuInfo;
use color_eyre::Result;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct GpuHealth {
    pub available: bool,
    pub message: String,
}

/// Abstraction over GPU/LLM runtime providers.
///
/// Each provider knows how to detect its hardware, configure model deployments,
/// check health, and produce the environment variables needed by the LLM stack.
/// Adding CUDA 13 support means adding a variant to `CudaVersion` — no changes
/// to backend or install logic.
pub trait GpuProvider: Send + Sync {
    fn name(&self) -> &str;
    fn detect_local(&self) -> Result<Option<Vec<GpuInfo>>>;
    fn health_check_env(&self) -> HashMap<String, String>;
    fn env_vars(&self) -> HashMap<String, String>;
}
