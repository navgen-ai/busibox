use super::GpuProvider;
use busibox_core::hardware::GpuInfo;
use color_eyre::Result;
use std::collections::HashMap;

pub struct MlxProvider;

impl GpuProvider for MlxProvider {
    fn name(&self) -> &str {
        "mlx"
    }

    fn detect_local(&self) -> Result<Option<Vec<GpuInfo>>> {
        Ok(None)
    }

    fn health_check_env(&self) -> HashMap<String, String> {
        HashMap::new()
    }

    fn env_vars(&self) -> HashMap<String, String> {
        let mut env = HashMap::new();
        env.insert("LLM_BACKEND".to_string(), "mlx".to_string());
        env
    }
}
