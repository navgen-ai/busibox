use super::GpuProvider;
use busibox_core::hardware::GpuInfo;
use color_eyre::Result;
use std::collections::HashMap;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CudaVersion {
    V12,
    V13,
}

impl std::fmt::Display for CudaVersion {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CudaVersion::V12 => write!(f, "12"),
            CudaVersion::V13 => write!(f, "13"),
        }
    }
}

pub struct CudaProvider {
    pub version: CudaVersion,
}

impl CudaProvider {
    pub fn new(version: CudaVersion) -> Self {
        Self { version }
    }
}

impl GpuProvider for CudaProvider {
    fn name(&self) -> &str {
        "vllm"
    }

    fn detect_local(&self) -> Result<Option<Vec<GpuInfo>>> {
        let output = std::process::Command::new("nvidia-smi")
            .args(["--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
            .output();

        match output {
            Ok(o) if o.status.success() => {
                let stdout = String::from_utf8_lossy(&o.stdout);
                let gpus: Vec<GpuInfo> = stdout
                    .lines()
                    .filter(|l| !l.trim().is_empty())
                    .filter_map(|line| {
                        let parts: Vec<&str> = line.splitn(2, ',').collect();
                        if parts.len() == 2 {
                            let name = parts[0].trim().to_string();
                            let vram_mb: u64 = parts[1].trim().parse().unwrap_or(0);
                            Some(GpuInfo { name, vram_gb: vram_mb / 1024 })
                        } else {
                            None
                        }
                    })
                    .collect();
                if gpus.is_empty() { Ok(None) } else { Ok(Some(gpus)) }
            }
            _ => Ok(None),
        }
    }

    fn health_check_env(&self) -> HashMap<String, String> {
        HashMap::new()
    }

    fn env_vars(&self) -> HashMap<String, String> {
        let mut env = HashMap::new();
        env.insert("LLM_BACKEND".to_string(), "vllm".to_string());
        env.insert("CUDA_VERSION".to_string(), self.version.to_string());
        env
    }
}
