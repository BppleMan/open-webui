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

### Stage 3 contracts for continuation

- `Connection.transport` is immutable after creation. `server` executes on OI; `browser` executes through the authenticated initiating socket. Native model IDs remain `oi:<uuid>`; display names, legacy array positions and provider IDs never identify the dispatch target.
- `POST /api/v1/connections/migration/preview` takes `scope=personal|server`, returns a credential-free source list and HMAC version. Apply takes that version and explicit per-source create/reuse decisions. Source locks, version comparison and durable receipts make retries atomic/idempotent; changing choices for an applied preview returns409. Existing target overrides are retained. Unsupported engines remain explicit compatibility items.
- `GET /api/v1/connections/{id}/browser-config?revision=...` is owner-only with no-store/private headers. The ordinary directory, profile/settings serialization and archives exclude credentials. Browser discovery uploads only model descriptors through the versioned `browser-models` endpoint. Legacy secrets are removed from migrated configuration in the same transaction that persists encrypted credentials.
- `POST /api/v1/connections/{id}/server-copy` verifies server model discovery before creating a private server connection. It returns source→target model mappings, preserves the source, and deduplicates concurrent retries by source revision. It does not silently redirect existing chats or drafts.
- Connection removal/restore uses revision checks. Removal preserves model/call records; restore leaves the connection disabled. Historical alias ambiguity includes removed identities, preventing deletion from redirecting old references to a sibling model.
- Browser RPC forwards the normalized native request and raw UTF8 response stream through the initiating browser; it reuses native protocol parsing, usage snapshots and ledger attribution. No active browser means no dispatch; loss of that session fails the call while preserving confirmed usage. HTTP429 is propagated without retry. Separate background execution must select server transport.
- Resume from OI5 with the same persistent server secret; additive migration does not reconstruct old accounting. Test fixtures use only synthetic input and loopback providers. Stage4 must prove each model entrypoint end-to-end rather than inferring correctness from shared components.


Stage5 is complete under the documented built-in-browser scope. See stage5-release.json and stage5-acceptance.json. Continue stage6 native protocols/context, stage7 costs/archive and stage8 staging/GitOps. Physical PWA was explicitly waived; production remains unchanged.


Stage7 implements atomic historical-cost JSON import, versioned cost replacement, partial/late usage preservation, fixed snapshot correction, canonical archive provenance and concurrent safe restore. See stage7-acceptance.md for the OI6 schema and portable archive contract; stage7-release.json records CI/image publication. Next is stage8 staging and GitOps; no production switch is included.

R50 latest boundary: finish stage8A prelaunch preparation and acceptance only. User intends further tuning. No production OI/CPA replacement, no maintenance activation, no production database migration. Stage8B requires new explicit authorization.

Stage8A is verified; current evidence is stage8-acceptance.json and stage8-release.json. Continue with user tuning. Stage8B production authorization has not been given.


R51/P36 preview refinement is verified at a198214b3. Context popovers use rendered capped height, connection lists expose five scrollable rows, composer controls align, settings options reuse Bits UI, and usage details distinguish confirmed/unknown data. CPA/AGY discovery and generation share native /v1beta routing; R52/P37 documents the protocol distinction. See preview-refinement-2026-10-29.json for CI, real synthetic CPA and browser evidence. Production remains unchanged under R50.

## R53 / P38 — protocol decoupling

Protocol registry, persisted service routing and OI7 model inheritance are implemented. Browser RPC carries explicit streaming and validates the resolved endpoint. The compact connection editor uses common mode choices and readonly URL previews. Final checks: 351 backend and 57 frontend tests, no new type/a11y diagnostics (5765 existing upstream diagnostics), production build and CI 40814642568 passed. Populated PostgreSQL migration, restore, browser user paths and isolated preview are verified. Real supplier/CORS boundaries are recorded in protocol-decoupling-acceptance.json; R50 holds. See protocol-decoupling.md.


### R54/P39 context Popover acceptance

The context popup now uses the installed Bits UI Popover primitive and one vertical scroll container. Source 3ea4c2b4a1a3c2501d592d51e56b075e471870b1 passed the final browser matrix, frontend tests, type delta, production build and CI. Only the isolated preview was updated. See context-popover-acceptance.json for the scroll measurements, CI and immutable image. U49 requires completing related edits before the batch of checks and preview publication. R50 remains in force.


### R55/P40 model editor refinement

Connection model regions now align with their parent row. Both editors use fixed viewport-bounded frames, independent body scrolling and fixed action footers. Single-line controls match the existing settings select height, with separate multiline JSON styling. Capacity displays shared context, independent input and output limits separately. Existing CPA discovery did not advertise the sampled limits; no values were invented or backend behavior changed. Source 98e09d6fd4de602f5b89aa1077710b63331b51c2 and CI40822743633 passed; isolated preview is verified. See model-editor-acceptance.json, including the pinned-dependency verification used after remote Helm refresh stalled. R50/U49 remain.
