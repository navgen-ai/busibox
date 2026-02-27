---
title: "Data Sovereignty"
category: "overview"
order: 1
description: "How Busibox keeps your data under your control — local processing, sensitivity classification, and compliance"
published: true
---

# Data Sovereignty

[Back to Why Busibox](00-why-busibox.md)

## The Core Principle

When you use a cloud AI platform, every document you upload, every question you ask, and every answer you receive passes through infrastructure you don't control. The provider's terms of service govern what happens to your data — and those terms can change.

Busibox takes a different approach: **your infrastructure, your data**. The entire platform — file storage, databases, vector indexes, LLM inference, and application hosting — runs on hardware you own or control. Nothing leaves your network unless you explicitly choose to send it.

## What Stays Local

Every layer of the Busibox stack is designed to run within your network boundary:

| Component | Where It Runs | What It Stores |
|-----------|---------------|----------------|
| **MinIO** (object storage) | Your server | Original documents and derived artifacts |
| **PostgreSQL** | Your server | File metadata, user accounts, permissions, conversations, audit logs |
| **Milvus** (vector database) | Your server | Document embeddings and search indexes |
| **Redis** | Your server | Processing queues and ephemeral state |
| **vLLM / MLX** (local LLMs) | Your GPU(s) | Nothing persistent — inference only |
| **FastEmbed** (embeddings) | Your CPU | Nothing persistent — embedding generation only |

When running with local models only, zero data leaves your network. There are no telemetry calls, no usage analytics sent externally, and no background syncing to cloud services.

## Local LLM Inference

Busibox supports running LLMs entirely on local hardware:

- **NVIDIA GPUs**: vLLM provides high-throughput inference with continuous batching, supporting quantized models (GPTQ, AWQ) for efficient memory usage.
- **Apple Silicon**: MLX runs natively on the Metal GPU, providing fast inference on Mac hardware without containerization overhead.

Local models handle the full range of AI tasks: chat responses, document analysis, schema extraction, text cleanup, and search reranking. For many use cases, a capable local model (24 GB VRAM or unified memory) is sufficient without any cloud dependency.

## Sensitivity Classification

Not all documents require the same level of protection. Busibox supports folder-level sensitivity classification that controls how data is processed:

- **Local-only folders**: Documents in these folders are processed exclusively by local models. Even if frontier models are configured, these documents will never be sent to a cloud provider.
- **Standard folders**: Documents can be processed by whichever model is most appropriate for the task, including cloud models if configured.

This classification travels with the data through the entire pipeline — from ingestion and embedding, through search and retrieval, to agent responses. When an agent answers a question, the sensitivity of the source documents determines which model can generate the response.

## Contrast with Cloud AI

| Aspect | Cloud AI Platforms | Busibox |
|--------|-------------------|---------|
| **Data location** | Provider's infrastructure | Your infrastructure |
| **Data processing** | Provider's models on provider's hardware | Your choice of local or cloud models |
| **Data retention** | Governed by provider's terms | Governed by your policies |
| **Access control** | Provider's permission model | Your RBAC + PostgreSQL Row-Level Security |
| **Audit trail** | Provider's logging (if available) | Your PostgreSQL audit tables, fully queryable |
| **Network boundary** | Data crosses your network | Data stays within your network (unless you choose otherwise) |
| **Model choice** | Provider's available models | Any model: local open-source, or any cloud provider |
| **Vendor lock-in** | Switching means re-ingesting everything | Open formats, standard APIs, no proprietary lock-in |

## Compliance and Audit

For organizations operating under regulatory frameworks (GDPR, HIPAA, SOX, industry-specific requirements), Busibox provides the infrastructure-level controls that compliance demands:

- **Data residency**: All data resides on your infrastructure in the jurisdiction you choose. No cross-border data transfers unless you configure cloud model access.
- **Audit trails**: Every authentication event, token exchange, and administrative action is logged to PostgreSQL. Audit records include timestamps, user identifiers, and action details.
- **Encryption at rest**: PostgreSQL and MinIO support encryption at rest. TLS encrypts data in transit between containers.
- **Access control**: Row-Level Security in PostgreSQL ensures that database queries only return rows the requesting user is authorized to see. This is enforced at the database level, below the application layer — it cannot be bypassed by application bugs.
- **Container isolation**: Each service runs in its own LXC or Docker container with dedicated resources. A vulnerability in one service does not grant access to another.

## Practical Implications

- **Legal review**: Upload confidential contracts and have AI analyze them without the documents ever leaving your premises.
- **HR processes**: Process resumes and employee data with AI assistance, maintaining full control over sensitive personal information.
- **Competitive intelligence**: Aggregate and analyze competitor data without exposing your analytical focus to third-party providers.
- **Client data**: Consultancies can process client materials knowing that data isolation is enforced at the infrastructure level, not just by application logic.

## Further Reading

- [Security Architecture](02-security-architecture.md) — Zero Trust authentication, RBAC, and RLS
- [Hybrid AI](03-hybrid-ai.md) — How local and cloud models work together
- [Platform Capabilities](04-platform-capabilities.md) — Document processing, search, and agents
