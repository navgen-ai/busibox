# Busibox (Proxmox IaC for secure file ingestion + RAG)

This repo provisions a role-secured data layer on a Proxmox host using LXC containers:
- **files-lxc**: MinIO (S3) for file storage + webhook events.
- **pg-lxc**: PostgreSQL for users/roles/metadata with RLS.
- **milvus-lxc**: Milvus Standalone (via Docker) for embeddings.
- **agent-lxc**: API gateway (Node) to enforce RBAC, issue presigned URLs, search Milvus.
- **ingest-lxc**: Worker to extract/chunk/embed and write to Milvus + Postgres.
- **queue**: Redis Streams running in **ingest-lxc** for ingestion jobs.

It also ships a **deploywatch** systemd timer to poll GitHub Releases and redeploy services.

> NOTE: You will execute the `provision/pct/*.sh` from the Proxmox host. The rest is applied inside each container via Ansible.
