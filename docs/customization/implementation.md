# Accepted implementation contract

Approved by the user on 2026-10-25. This is the implementation index; requirements.md preserves the original requirements and later corrections.

1. Fork Open WebUI v0.12.3 publicly under BppleMan, retain upstream and license/branding. Private records never enter Git or the image.
2. Add additive connection/model identity, draft, generation/call, cost snapshot/import baseline and UI metadata persistence. SQLAlchemy/Alembic, PostgreSQL production and SQLite development.
3. Renew valid human sessions without weakening revocation/MFA; preserve auth_time. Restore expired login without losing drafts. Ordinary chats have one versioned server draft per user/chat, IndexedDB protects pending text; temporary chats remain device-only. Allocate stable chat identity on first input.
4. Group ALL 22 inventoried model surfaces by concrete connection. Preserve duplicate upstream IDs, permissions, historical identity and capability filtering. Ambiguous migration is explicit.
5. Native OpenAI Chat/Responses, Anthropic Messages, Gemini GenerateContent/Interactions, existing Ollama and CPA/AGY. Preserve native blocks, tools, signatures, metadata and usage; CPA owns OAuth/scheduling and retains RP/429 semantics. Templates cover the previously listed official, gateway, compatible and local services.
6. Each generation and underlying call is independently recorded before dispatch with idempotency. Actual USD and estimated USD stay separate; unknown stays unknown. Prices and source versions are snapshotted. Per-round cumulative snapshots have ledger boundaries. Fork inherits its anchor snapshot; parent calls after the anchor do not enter the fork. Late settlements update covered calls without double-counting. Import baselines replace the same batch and exclude duplicate historical item cost.
7. Native message usage and LC context panel, capability-based reasoning controls, modal body editing/revisions, compact settings rows (48rem form, 1rem label/control gap, 24rem slider). Default no compaction/truncation or hidden RP context.
8. Named branches, response comparison/manual adoption, bookmarks/read position, complete archive round trips with attachments/identity/accounting. Reuse existing features first; no extra inference for UI features.
9. Test fees 0.03+0.04+0.05=0.12; fork+0.02=0.14; duplicate chunks, reconnection, partial usage, revocation, >5000-character drafts, concurrent edits, PWA termination, duplicate model IDs, native stream/nonstream/tool contracts, CPA429, unchanged normalized prompts, mobile/desktop, PostgreSQL migration/restore.
10. Build immutable image; isolated preview DB/Redis/attachments; backup and restore drill; GitOps cutover only after accounting and user-path checks. No simultaneous upstream/custom writers. Roll back only to schema-compatible custom versions after new writes. Feature status separates code, tests and deployment.

Deferred: canon/state injection, automatic memories, campaign/character systems, RP writing rules/request inspector, dice, GM knowledge scopes, independent RP sampling presets. Existing background generation and advanced parameter controls are reused.


Stage3 implements transport-aware connection identity, versioned migration and owner-only browser credentials. See validation.md for local acceptance and actual CI status. Continue with stages4–8 after this checkpoint; no production release in stage3.
