/// Service registry — Rust port of scripts/lib/backends/common.sh.
///
/// Centralizes service group definitions, name mappings, Ansible tags,
/// and companion service logic so both the full TUI and quick installer
/// can share them without shelling out.

/// Service groups in display order.
pub const GROUP_ORDER: &[&str] = &["Frontend", "APIs", "LLM", "Infrastructure"];
pub const GROUP_ORDER_K8S: &[&str] = &["Frontend", "APIs", "LLM", "Infrastructure", "Build"];

pub fn group_order(backend: &str) -> &'static [&'static str] {
    if backend == "k8s" { GROUP_ORDER_K8S } else { GROUP_ORDER }
}

/// Get services belonging to a group for a given backend.
pub fn services_for_group(group: &str, backend: &str, is_mlx: bool) -> Vec<&'static str> {
    match group {
        "Infrastructure" => {
            let mut v = vec!["nginx", "redis", "minio", "postgres", "milvus", "neo4j"];
            if backend == "k8s" {
                v.push("etcd");
            }
            v
        }
        "Build" => {
            if backend == "k8s" {
                vec!["build-server", "registry"]
            } else {
                vec![]
            }
        }
        "APIs" => vec![
            "deploy", "authz", "agent", "data", "embedding", "search",
            "bridge", "docs", "config",
        ],
        "LLM" => {
            if backend == "k8s" {
                vec!["litellm"]
            } else if is_mlx {
                vec!["litellm", "mlx"]
            } else {
                vec!["litellm", "vllm"]
            }
        }
        "Frontend" => {
            if backend == "k8s" {
                vec!["proxy"]
            } else {
                vec!["core-apps", "user-apps", "custom-services"]
            }
        }
        _ => vec![],
    }
}

/// Map a user-facing service name to the Docker container name (without prefix).
pub fn container_for_service(service: &str) -> Option<&'static str> {
    match service {
        "postgres" | "pg" => Some("postgres"),
        "redis" => Some("redis"),
        "minio" | "files" => Some("minio"),
        "milvus" => Some("milvus"),
        "neo4j" | "graph" => Some("neo4j"),
        "etcd" => Some("etcd"),

        "authz" | "authz-api" => Some("authz-api"),
        "agent" | "agent-api" => Some("agent-api"),
        "ingest" | "data" | "data-api" => Some("data-api"),
        "data-worker" => Some("data-worker"),
        "search" | "search-api" => Some("search-api"),
        "deploy" | "deploy-api" => Some("deploy-api"),
        "config" | "config-api" => Some("config-api"),
        "bridge" | "bridge-api" => Some("bridge-api"),
        "docs" | "docs-api" => Some("docs-api"),
        "embedding" | "embedding-api" => Some("embedding-api"),

        "litellm" => Some("litellm"),
        "vllm" => Some("vllm"),

        "mlx" => Some("mlx"),
        "host-agent" => Some("host-agent"),

        "core-apps" | "apps" | "busibox-portal" | "busibox-admin"
        | "busibox-agents" | "busibox-chat" | "busibox-appbuilder"
        | "busibox-media" | "busibox-documents" => Some("core-apps"),
        "nginx" | "proxy" => Some("proxy"),

        "user-apps" => Some("user-apps"),
        "custom-services" => Some("custom-services"),

        "build-server" => Some("build-server"),
        "registry" => Some("registry"),

        _ => None,
    }
}

/// Map a service to its Ansible tag (for Docker deployment via docker.yml).
pub fn ansible_tag(service: &str) -> &str {
    match service {
        s if s.starts_with("authz") => "authz",
        s if s.starts_with("agent") => "agent",
        "ingest" | "data" | "data-api" | "data-worker" => "data",
        s if s.starts_with("search") => "search",
        "config" | "config-api" => "config",
        s if s.starts_with("deploy") => "deploy",
        s if s.starts_with("docs") => "docs",
        s if s.starts_with("embedding") => "embedding",
        "postgres" | "pg" => "postgres",
        "redis" => "redis",
        "minio" | "files" => "minio",
        "milvus" => "milvus",
        "neo4j" | "graph" => "neo4j",
        "core-apps" | "apps" | "busibox-portal" | "busibox-admin"
        | "busibox-agents" | "busibox-chat" | "busibox-appbuilder"
        | "busibox-media" | "busibox-documents" => "core-apps",
        "nginx" | "proxy" => "nginx",
        "custom-services" => "custom_services",
        other => other,
    }
}

/// Map a service to its Proxmox make target (for site.yml deployment).
pub fn proxmox_make_target(service: &str) -> &str {
    match service {
        "postgres" | "pg" => "pg",
        "minio" | "files" => "files",
        "milvus" => "milvus",
        "neo4j" | "graph" => "neo4j",
        "redis" => "data",
        "authz" | "authz-api" => "authz",
        "agent" | "agent-api" => "agent",
        "ingest" | "data" | "data-api" | "data-worker" => "data",
        "search" | "search-api" => "search-api",
        "deploy" | "deploy-api" => "deploy-api",
        "config" | "config-api" => "config-api",
        "bridge" | "bridge-api" => "bridge",
        "docs" | "docs-api" => "docs",
        "embedding" | "embedding-api" => "embedding-api",
        "litellm" => "litellm",
        "vllm" => "vllm",
        "core-apps" | "apps" => "apps",
        "busibox-portal" => "deploy-busibox-portal",
        "busibox-agents" => "deploy-busibox-agents",
        "busibox-appbuilder" => "deploy-busibox-appbuilder",
        "nginx" | "proxy" => "nginx",
        "user-apps" => "user-apps",
        "custom-services" => "custom_services",
        other => other,
    }
}

/// Get companion services that should be managed together with a service.
pub fn companion_services(service: &str) -> Vec<&'static str> {
    match service {
        "data-api" | "ingest" | "data" => vec!["data-worker"],
        _ => vec![],
    }
}

/// Whether a service runs on the host natively (not in a container).
pub fn is_host_native(service: &str) -> bool {
    matches!(service, "mlx" | "host-agent")
}

/// Whether a service name is recognized.
pub fn is_valid_service(service: &str) -> bool {
    container_for_service(service).is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn group_order_default_has_four_groups() {
        assert_eq!(group_order("docker").len(), 4);
        assert_eq!(group_order("proxmox").len(), 4);
    }

    #[test]
    fn group_order_k8s_includes_build() {
        let order = group_order("k8s");
        assert_eq!(order.len(), 5);
        assert!(order.contains(&"Build"));
    }

    #[test]
    fn infrastructure_includes_etcd_for_k8s_only() {
        let docker_infra = services_for_group("Infrastructure", "docker", false);
        assert!(!docker_infra.contains(&"etcd"));

        let k8s_infra = services_for_group("Infrastructure", "k8s", false);
        assert!(k8s_infra.contains(&"etcd"));
    }

    #[test]
    fn llm_group_varies_by_backend_and_gpu() {
        let k8s = services_for_group("LLM", "k8s", false);
        assert_eq!(k8s, vec!["litellm"]);

        let docker_mlx = services_for_group("LLM", "docker", true);
        assert!(docker_mlx.contains(&"mlx"));
        assert!(!docker_mlx.contains(&"vllm"));

        let docker_vllm = services_for_group("LLM", "docker", false);
        assert!(docker_vllm.contains(&"vllm"));
        assert!(!docker_vllm.contains(&"mlx"));
    }

    #[test]
    fn frontend_group_docker_vs_k8s() {
        let docker = services_for_group("Frontend", "docker", false);
        assert!(docker.contains(&"core-apps"));

        let k8s = services_for_group("Frontend", "k8s", false);
        assert_eq!(k8s, vec!["proxy"]);
    }

    #[test]
    fn build_group_only_for_k8s() {
        assert!(services_for_group("Build", "docker", false).is_empty());
        assert!(!services_for_group("Build", "k8s", false).is_empty());
    }

    #[test]
    fn unknown_group_returns_empty() {
        assert!(services_for_group("Nonexistent", "docker", false).is_empty());
    }

    #[test]
    fn container_for_service_infrastructure_aliases() {
        assert_eq!(container_for_service("postgres"), Some("postgres"));
        assert_eq!(container_for_service("pg"), Some("postgres"));
        assert_eq!(container_for_service("minio"), Some("minio"));
        assert_eq!(container_for_service("files"), Some("minio"));
        assert_eq!(container_for_service("neo4j"), Some("neo4j"));
        assert_eq!(container_for_service("graph"), Some("neo4j"));
    }

    #[test]
    fn container_for_service_api_aliases() {
        assert_eq!(container_for_service("authz"), Some("authz-api"));
        assert_eq!(container_for_service("authz-api"), Some("authz-api"));
        assert_eq!(container_for_service("data"), Some("data-api"));
        assert_eq!(container_for_service("ingest"), Some("data-api"));
        assert_eq!(container_for_service("data-api"), Some("data-api"));
    }

    #[test]
    fn container_for_service_frontend_aliases() {
        for alias in &["core-apps", "apps", "busibox-portal", "busibox-admin",
                       "busibox-agents", "busibox-chat", "busibox-appbuilder",
                       "busibox-media", "busibox-documents"] {
            assert_eq!(container_for_service(alias), Some("core-apps"),
                       "alias '{alias}' should map to core-apps");
        }
        assert_eq!(container_for_service("nginx"), Some("proxy"));
        assert_eq!(container_for_service("proxy"), Some("proxy"));
    }

    #[test]
    fn container_for_service_unknown_returns_none() {
        assert_eq!(container_for_service("nonexistent"), None);
        assert_eq!(container_for_service(""), None);
    }

    #[test]
    fn ansible_tag_maps_correctly() {
        assert_eq!(ansible_tag("authz"), "authz");
        assert_eq!(ansible_tag("authz-api"), "authz");
        assert_eq!(ansible_tag("data"), "data");
        assert_eq!(ansible_tag("data-worker"), "data");
        assert_eq!(ansible_tag("ingest"), "data");
        assert_eq!(ansible_tag("postgres"), "postgres");
        assert_eq!(ansible_tag("pg"), "postgres");
        assert_eq!(ansible_tag("core-apps"), "core-apps");
        assert_eq!(ansible_tag("busibox-portal"), "core-apps");
        assert_eq!(ansible_tag("nginx"), "nginx");
        assert_eq!(ansible_tag("proxy"), "nginx");
    }

    #[test]
    fn ansible_tag_unknown_passes_through() {
        assert_eq!(ansible_tag("custom-service"), "custom-service");
    }

    #[test]
    fn proxmox_make_target_maps_correctly() {
        assert_eq!(proxmox_make_target("postgres"), "pg");
        assert_eq!(proxmox_make_target("pg"), "pg");
        assert_eq!(proxmox_make_target("minio"), "files");
        assert_eq!(proxmox_make_target("redis"), "data");
        assert_eq!(proxmox_make_target("core-apps"), "apps");
        assert_eq!(proxmox_make_target("busibox-portal"), "deploy-busibox-portal");
        assert_eq!(proxmox_make_target("busibox-agents"), "deploy-busibox-agents");
        assert_eq!(proxmox_make_target("busibox-appbuilder"), "deploy-busibox-appbuilder");
    }

    #[test]
    fn proxmox_make_target_unknown_passes_through() {
        assert_eq!(proxmox_make_target("my-custom"), "my-custom");
    }

    #[test]
    fn companion_services_data_gets_worker() {
        assert_eq!(companion_services("data-api"), vec!["data-worker"]);
        assert_eq!(companion_services("data"), vec!["data-worker"]);
        assert_eq!(companion_services("ingest"), vec!["data-worker"]);
    }

    #[test]
    fn companion_services_other_empty() {
        assert!(companion_services("authz").is_empty());
        assert!(companion_services("postgres").is_empty());
    }

    #[test]
    fn host_native_services() {
        assert!(is_host_native("mlx"));
        assert!(is_host_native("host-agent"));
        assert!(!is_host_native("postgres"));
        assert!(!is_host_native("authz"));
    }

    #[test]
    fn is_valid_service_recognizes_known() {
        assert!(is_valid_service("postgres"));
        assert!(is_valid_service("authz"));
        assert!(is_valid_service("busibox-portal"));
        assert!(is_valid_service("mlx"));
    }

    #[test]
    fn is_valid_service_rejects_unknown() {
        assert!(!is_valid_service("nonexistent"));
        assert!(!is_valid_service(""));
    }
}
