use super::GpuProvider;
use busibox_core::hardware::GpuInfo;
use color_eyre::Result;
use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq)]
pub enum CloudProvider {
    OpenAI,
    Anthropic,
    Bedrock,
}

impl std::fmt::Display for CloudProvider {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CloudProvider::OpenAI => write!(f, "openai"),
            CloudProvider::Anthropic => write!(f, "anthropic"),
            CloudProvider::Bedrock => write!(f, "bedrock"),
        }
    }
}

pub struct CloudLlmProvider {
    pub provider: CloudProvider,
    pub api_key: Option<String>,
}

impl CloudLlmProvider {
    pub fn new(provider: CloudProvider, api_key: Option<String>) -> Self {
        Self { provider, api_key }
    }
}

impl GpuProvider for CloudLlmProvider {
    fn name(&self) -> &str {
        "cloud"
    }

    fn detect_local(&self) -> Result<Option<Vec<GpuInfo>>> {
        Ok(None)
    }

    fn health_check_env(&self) -> HashMap<String, String> {
        HashMap::new()
    }

    fn env_vars(&self) -> HashMap<String, String> {
        let mut env = HashMap::new();
        env.insert("LLM_BACKEND".to_string(), "cloud".to_string());
        env.insert("CLOUD_PROVIDER".to_string(), self.provider.to_string());
        if let Some(ref key) = self.api_key {
            match self.provider {
                CloudProvider::OpenAI => { env.insert("OPENAI_API_KEY".to_string(), key.clone()); }
                CloudProvider::Anthropic => { env.insert("ANTHROPIC_API_KEY".to_string(), key.clone()); }
                CloudProvider::Bedrock => { env.insert("CLOUD_API_KEY".to_string(), key.clone()); }
            }
        }
        env
    }
}
