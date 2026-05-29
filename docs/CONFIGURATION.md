# Configuration Reference

AIP 0.1 configuration is loaded from `config/aip.config.toml`. All parameters are toggleable per §1.8 (Harness Evolution Principle).

---

## `[retrieval]`

Retrieval harness parameters for the hybrid retrieval pipeline.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `confidence_threshold` | float | 0.30 | Minimum confidence for retrieval results |
| `weight_semantic` | float | 0.60 | Weight for vector similarity in hybrid scoring |
| `weight_recency` | float | 0.15 | Weight for recency in hybrid scoring |
| `weight_authority` | float | 0.15 | Weight for source authority in hybrid scoring |
| `weight_frequency` | float | 0.10 | Weight for access frequency in hybrid scoring |

---

## `[embedding]`

Embedding provider configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | string | `"fake"` | Provider: `"fake"` (deterministic), `"local"` (sentence-transformers), `"api"` (remote) |
| `model` | string | — | Model name for local/api provider (e.g., `"sentence-transformers/all-MiniLM-L6-v2"`) |

---

## `[sexton]`

Sexton failure classification actor parameters.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `classification_batch_size` | int | 50 | Events processed per classification run |
| `classification_interval_seconds` | int | 300 | Seconds between classification runs (5 min) |
| `audit_on_slot_change` | bool | true | Audit model_gen_assumptions when model slot changes |
| `max_unclassified_before_alert` | int | 10 | Alert threshold for unclassified failures |

---

## `[ace_playbook]`

ACE Playbook procedural intervention rules.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `db_path` | string | `"db/ace_playbook.db"` | SQLite path for playbook storage |
| `auto_derive` | bool | true | Sexton auto-derives entries from classifications |
| `min_confidence` | float | 0.70 | Minimum Sexton confidence to auto-promote entry |

---

## `[router]`

Model routing configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `default_exploration_weight` | float | 0.10 | Probability of non-optimal model routing |
| `min_sample_count` | int | 10 | Minimum routing outcomes before adjusting weights |
| `weight_decay` | float | 0.95 | Exponential decay for old routing outcomes |
| `domain_overrides` | dict | `{}` | Per-domain exploration_weight overrides |

---

## `[budget]`

Token budget enforcement.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `session_token_limit` | int | 500000 | Max tokens per session |
| `project_token_limit` | int | 5000000 | Max tokens per project |
| `daily_token_limit` | int | 10000000 | Max tokens per day |
| `budget_warning_threshold` | float | 0.80 | Fraction at which warning events are emitted |
| `budget_hard_stop` | bool | true | Block calls when budget is exhausted |

---

## `[beast]`

Beast maintenance actor cadence.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `corpus_reindex_interval_seconds` | int | 3600 | Reindex interval (1 hour) |
| `entity_maintenance_interval_seconds` | int | 1800 | Entity maintenance interval (30 min) |
| `health_check_interval_seconds` | int | 60 | Health check interval |
| `max_reindex_batch_size` | int | 1000 | Max vectors per reindex batch |

---

## `[api]`

REST API surface configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `host` | string | `"127.0.0.1"` | Bind address |
| `port` | int | 8000 | Bind port |
| `cors_origins` | list | `["http://localhost:3000"]` | Allowed CORS origins |
| `workers` | int | 1 | Uvicorn worker count |
| `chat_max_history_turns` | int | 50 | Max conversation turns retained |
| `review_page_size` | int | 20 | Review queue page size |
| `artifact_page_size` | int | 20 | Artifact list page size |

---

## `[cli]`

CLI surface configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `color` | bool | true | Enable colored output |
| `pager` | bool | true | Enable pager for long output |
| `output_format` | string | `"table"` | Output format: `table`, `json`, `yaml` |

---

## `[mcp]`

MCP (Model Context Protocol) server configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | true | Enable MCP server |
| `transport` | string | `"stdio"` | Transport: `stdio` or `sse` |
| `max_concurrent_tools` | int | 5 | Max concurrent tool calls |

---

## `[chat]`

Chat surface configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `system_prompt_path` | string | `"prompts/chat_system.md"` | Path to system prompt template |
| `max_context_turns` | int | 50 | Max turns before context reset |
| `auto_summarize_at` | int | 40 | Turn count that triggers summarization |

---

## `[autonomy]`

Autonomy gate configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `default_level` | string | `"read"` | Default autonomy for new sessions |
| `escalation_requires_definer` | bool | true | Admin actions require DEFINER approval |
| `audit_retention_days` | int | 90 | Days to retain audit log |
| `model_gen_assumption` | string | — | Assumption about model escalation behavior |

---

## `[lexical]`

Lexical search (FTS5) configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `db_path` | string | `"db/lexical.db"` | SQLite path for FTS5 index |
| `fts5_tokenizer` | string | `"unicode61"` | FTS5 tokenizer |

---

## `[review]`

Review gate thresholds.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `faithfulness_threshold` | float | 0.85 | Minimum faithfulness score to pass review |
| `domain_coherence_threshold` | float | 0.80 | Minimum domain coherence to pass review |
| `require_definer_approval` | bool | true | REQUIRE DEFINER approval for canonical promotion |
| `auto_promote_on_approval` | bool | false | Auto-promote without explicit action |

---

## `[workflow]`

Workflow engine configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `default_template` | string | `"workflow_01"` | Default workflow template |
| `max_parallel_nodes` | int | 3 | Max nodes executing in parallel |
| `context_reset_turn_threshold` | int | 40 | Turn count triggering context reset |

---

## `[ecs]`

ECS store configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `db_path` | string | `"db/ecs.db"` | SQLite path for ECS transitions |
| `transition_audit_enabled` | bool | true | Record all transitions for audit |

---

## `[trajectory]`

L4 trajectory regulation configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `loop_detection_window` | int | 5 | Number of outputs to check for loops |
| `anxiety_threshold` | float | 0.7 | Anxiety score threshold for reset trigger |
| `failure_streak_threshold` | int | 3 | Consecutive failures before intervention |
| `context_reset_on_anxiety` | bool | true | Reset context when anxiety exceeds threshold |

---

## `[evaluation]`

Evaluation pipeline configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `faithfulness_threshold` | float | 0.85 | Faithfulness pass threshold |
| `domain_coherence_threshold` | float | 0.80 | Domain coherence pass threshold |
| `adversarial_enabled` | bool | false | Enable adversarial evaluation |

---

## `[vigil]`

Vigil actor configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `canonical_health_check_interval_seconds` | int | 3600 | Health check cadence |
| `stale_threshold_days` | int | 30 | Days before canonical is considered stale |
| `re_evaluate_on_slot_change` | bool | true | Re-evaluate when model slot changes |
| `max_re_evaluate_batch_size` | int | 50 | Max artifacts per re-evaluation batch |
| `entity_consistency_check` | bool | true | Check entity consistency |

---

## `[auth]`

Authentication and authorization.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `auth_enabled` | bool | false | Enable authentication (laptop: false, production: true) |
| `session_timeout_seconds` | int | 86400 | Session token lifetime (24 hours) |
| `api_key_enabled` | bool | true | Allow API key authentication |
| `bcrypt_rounds` | int | 12 | Password hashing rounds |
| `definer_identity` | string | `"definer"` | The DEFINER identity name |

---

## `[rate_limit]`

Rate limiting configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | true | Enable rate limiting |
| `requests_per_minute` | int | 60 | Token bucket refill rate |
| `burst_size` | int | 10 | Token bucket burst capacity |
| `per_endpoint_overrides` | dict | `{}` | Per-endpoint RPM overrides |
| `model_budget_protection` | bool | true | Don't rate-limit reads (protect model budget) |

---

## `[canonical_pipeline]`

Canonical promotion pipeline configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `faithfulness_threshold` | float | 0.85 | Minimum faithfulness for promotion |
| `domain_coherence_threshold` | float | 0.80 | Minimum coherence for promotion |
| `require_vigil_health_check` | bool | true | Require healthy Vigil status |
| `indexing_enabled` | bool | true | Re-index on promotion |
| `require_definer_approval` | bool | true | Require DEFINER gate |

---

## `[knowledge]`

Knowledge compilation configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `compilation_model_slot` | string | `"synthesis"` | Model slot for compilation |
| `evaluation_model_slot` | string | `"evaluation"` | Model slot for evaluation |
| `max_source_canonicals` | int | 10 | Max source canonicals per compilation |
| `compilation_confidence_threshold` | float | 0.60 | Minimum confidence for auto-promotion |
| `auto_index_on_approval` | bool | true | Index compiled knowledge on approval |

---

## `[plugins]`

Plugin system configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `plugins_dir` | string | `"plugins"` | Plugin directory |
| `enabled` | bool | true | Enable plugin system |
| `auto_discover` | bool | true | Auto-discover plugins |
| `sandbox_mode` | bool | true | Run plugins in sandbox |

---

## `[collaborators]`

Collaborator access configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | false | Enable collaborator access |
| `max_collaborators` | int | 5 | Maximum number of collaborators |
| `collaborator_can_create_drafts` | bool | true | Allow draft creation |
| `collaborator_can_submit_review` | bool | true | Allow review submission |
| `collaborator_can_approve` | bool | false | **Always false per §1.7** |
| `readonly_can_search` | bool | true | Allow search for readonly users |

---

## `[performance]`

Performance tuning.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `profiling_enabled` | bool | false | Enable profiling |
| `max_memory_mb` | int | 4096 | Memory limit in MB |
| `retrieval_timeout_seconds` | float | 30.0 | Retrieval operation timeout |
| `batch_embed_size` | int | 32 | Batch size for embedding |
| `sqlite_wal_mode` | bool | true | Enable WAL journal mode |
| `sqlite_busy_timeout_ms` | int | 5000 | SQLite busy timeout |
| `vector_query_limit` | int | 50 | Max vector query results |
| `fts5_query_limit` | int | 50 | Max FTS5 query results |

---

## `[deployment]`

Deployment profile configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `profile_name` | string | `"laptop"` | Profile: `"laptop"` or `"production"` |
| `vector_backend` | string | `"sqlite_vss"` | Backend: `"sqlite_vss"` or `"pgvector"` |
| `model_provider` | string | `"ollama"` | Provider: `"ollama"` or `"api"` |
| `auth_enabled` | bool | false | Enable authentication |
| `workers` | int | 1 | Worker count |
| `memory_limit_mb` | int | 4096 | Memory limit |

---

## `[release]`

Release metadata.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `release_version` | string | `"0.1.0"` | Version string |
| `release_date` | string | `""` | ISO 8601 date (set at release) |
| `release_status` | string | `"alpha"` | Release status |
| `architecture_revision` | string | `"5.2"` | Architecture document revision |
