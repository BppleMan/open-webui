# OI customization

Read docs/customization/requirements.md, prototype.md, implementation.md and features.json before changes. The user has approved implementation of this contract. Keep feature implementation, verification and deployment statuses separate.

- Base: upstream v0.12.5, merged from the original v0.12.3 fork. Preserve upstream licensing and branding.
- Every model list/selector uses connection-to-model identity, including capability-specific lists.
- Preserve native structured content and CPA RP semantics. Do not silently add prompts or truncate history.
- Record every generation/call once; actual and estimated USD are separate, unknown is not zero. Forks inherit anchor cost snapshots, not duplicate charges.
- Ordinary chats have one server draft per user/chat; temporary drafts stay on-device.
- Reuse background generation, search, forks and advanced controls. No separate RP sampling system.
- Campaign/character systems, automatic memory/state injection, writing directives/request inspector, dice and GM knowledge scopes are deferred.
- Keep credentials, private source discussions, runtime configuration, private screenshots and deployment evidence outside this public checkout. Public tests use synthetic fixtures.
- Formal deployment follows the separate GitOps repository after isolated migration and user-path acceptance.
- Stage 8A prelaunch is verified; read docs/customization/stage8-acceptance.json and stage8-release.json. The current next step is user tuning. The preview uses a fixed CI image with independent DB/Redis/PVC; production remains upstream. Do not repeat completed stages or interpret continuation as production authorization. Keep the documented supplier, browser and alias-decision boundaries. Stage 8B requires a new explicit user decision.

- Latest user boundary R50: complete prelaunch stage8A only, then hand off for user tuning. Production OI/CPA, routes and databases must remain unchanged. Earlier full-plan deployment authorization is superseded; stage8B requires new explicit authorization.

- Latest preview refinement R51/R52 is verified; read docs/customization/preview-refinement-2026-10-29.json. CPA/AGY uses /v1beta; do not generalize this to other CPA protocols. R50 production hold remains in force.

- R53 supersedes R52 address entry: enter a service root; protocol registry generates versioned endpoints. Read docs/customization/protocol-decoupling.md. Template defaults do not restrict protocol choices. Preserve migrated effective routes, model identity, browser ownership and R50.

- R53 is verified on isolated preview; read docs/customization/protocol-decoupling-acceptance.json. OI7 needs a compatible image; preserved legacy routes can require explicit review. Real OpenRouter browser Messages CORS remains a documented provider limitation. R50 remains unchanged.

- R54/P39: use existing Bits UI overlay primitives for context popovers. Verify real scrolling to both ends, collision placement, outside dismissal and keyboard focus; geometry or visible scrollbars alone are insufficient.

- User U49: finish the related edits as a batch before running browser acceptance, tests, type checks and build. Publish one validated preview update per batch; avoid full checks and image builds for every small edit.

- R54 is verified on the isolated preview. Read docs/customization/context-popover-acceptance.json; keep the existing Bits UI Popover and single vertical scroll container. Continue user tuning under R50.

- R55/P40 is verified on isolated preview; read docs/customization/model-editor-acceptance.json. Preserve parent-aligned model regions, fixed editor frames/footers, one scrolling body and equal single-line/select heights. Do not invent provider capacity when discovery omits it.

- R56/P41 is verified; read docs/customization/message-info-acceptance.json. Keep one native info entry for usage/cost/raw metadata, right/down disclosures, compact reply editing with undo-aware textarea sizing, and one-line composer model triggers with connection identity retained in menus/title/ARIA.

- R57/P42 supersedes R56 summary placement: essential usage belongs before the reply body, while info keeps technical/raw details. Keep disclosure state independent of ledger polling, fixed-height context popovers, and bounded tabbed reply editing.

- R57/P42 is verified on isolated preview; read docs/customization/usage-editor-acceptance.json. Keep reply-leading metrics, polling-independent disclosures, fixed context frames and compact tabbed editing. R50/U49 remain.

- R58/P43 and R59/P44 are discussion/diagnosis, not implemented: the user requested a design discussion first. Info must retain full usage/cost details alongside a minimal external summary, without nested disclosures. Reasoning usage does not guarantee returned readable thoughts; preserve this distinction and do not regenerate paid requests for diagnosis. Runtime remains R57 under R50.

- U56 approves implementing R58/P43: a separate wrapping metadata row with input/output/cache read/cache write/current cost always visible, restrained icons/colors and complete flat info tabs. R59 request controls remain proposed; keep supplier requests unchanged. R50/U49 apply.

- R58/P43/U56 is verified on isolated preview; read docs/customization/usage-density-acceptance.json. Keep the independent wrapping row and full flat info, unknown/zero distinctions, decimal amounts and stable source selection. R59 summary-request controls remain proposed.

- U58 authorizes R59/P44 Gemini streaming thought-summary requests: default on, explicit off preserved, native controls for both transports. R60/P45 addresses expired-session recovery and repeated rejected refreshes. Work is in progress; validate as one batch and publish only isolated preview under R50/U49. Never renew expired or revoked sessions by weakening authentication rules.

- R59/R60 code is deployed to isolated preview; read docs/customization/thoughts-session-acceptance.json. Synthetic server/browser summary, persistence, controls and CI pass; one actual CPA stream still returned only reasoning usage/signature, with no readable summary. Keep that supplier acceptance gap open and do not repeat the guarded paid probe. R60 recovery is verified. R50 production hold remains.

- R61/P46 supersedes natural wrapping on narrow screens: use explicit responsive grid groups, shared usage icons in info, and padded connection headings. Read docs/customization/usage-responsive-acceptance.json for current checks/deployment; retain R50, U49 and the separate R59 supplier gap.

- R62/P47: upstream v0.12.5 sync and same-row mobile current/cumulative cost. Read docs/customization/upstream-v0125-acceptance.json for merge, checks and runtime status. Preserve native history, call-based statistics, durable drafts, stable connection identities and R50. Do not reset the type baseline from modified application code.

- R62/P47 is verified on isolated preview; upstream base is v0.12.5. Full CI, a pristine type baseline, long draft reloads, native transport/regen and mobile exact-fee rows are recorded in upstream-v0125-acceptance.json. Continue user tuning; R50 and the R59 supplier gap remain.

- R63/P48 repairs the 14 full-history review findings; read docs/customization/review-remediation.md and review-remediation-acceptance.json. Preserve authenticated temporary native state, atomic cost inheritance, composite message identity, immutable ACL provenance and R50.

- R63/P48 is verified on isolated preview. The original 14 findings are remediated; full CI and loopback temporary chat acceptance are recorded in review-remediation-acceptance.json. Keep R50 and the R59 real-provider gap.

- R64/P49 repairs the eleven second-review findings; read docs/customization/review-round2-remediation.md and its acceptance record. Preserve temporary lifecycle boundaries, native terminal/media outputs, retrieval errors, trusted historical usage and discovery revision checks. R50/U49/R59 remain.

- R64/P49 is verified on isolated preview. The eleven second-review findings and adjacent temporary-draft retirement edge cases are repaired. Exact-source CI and built-in browser evidence are recorded separately. Preserve R50 and the R59 real-provider gap.

- R65/P50 repairs the seven third-review findings. Read docs/customization/review-round3-remediation.md and its acceptance record. Preserve shared ledger/caller separation, native-state privacy, metering-only temporary ledgers and guarded draft transfer. R50/U49/R59 remain.

- R65/P50 is verified on isolated preview. Preserve the guarded temporary save, chat-owned/caller-attributed ledger, non-owner native projection and metering-only Ollama usage. Original review reports remain historical, and existing records were not retrospectively rewritten. R50 and the R59 supplier gap remain.

- R66/P51 repairs the six fourth-review boundaries; read review-round4-remediation.md and its acceptance record. Separate private provider metadata from portable replay, preserve pure media history, invalidate outlet changes and guard the complete temporary save lifecycle. R50/U49/R59 remain.

- R66/P51 code and exact-source CI are verified; isolated preview publication is pending a GitHub egress failure. GitOps already points to the new image and automated sync may apply it when network access recovers. Recheck runtime and browser before marking deployed. Do not change shared proxy/production routes under R50. See review-round4-remediation-acceptance.json.

- R67/P52 repairs the four fifth-review boundaries. Read review-round5-remediation.md and its acceptance record. Preserve complete response privacy, terminal semantics, generated tool-ID sidecars and successful-save transitions despite sidebar failures. R50/U49/R59 remain.


R67/P52 code and exact-source CI are verified (507 backend/79 frontend). Browser acceptance and preview publication are pending the required in-app browser connection. Do not use an external browser. R66 is now observed Synced/Healthy; preserve its original historical evidence. See review-round5-remediation-acceptance.json.

- R68/P53 repairs the three sixth-review findings. Read review-round6-remediation.md and its acceptance record. Preserve filtered request content, surviving tool pairs, visible media and unchanged native signatures. R67 built-in-browser acceptance is carried forward before a combined isolated preview update. R50/U49/R59 remain.


R68/P53 code and exact-source CI are verified (581 backend/79 frontend). The required in-app browser is still not connected; R67/R68 browser acceptance and combined preview publication remain pending. Do not use an external browser or claim tests are browser acceptance.

- R69/P54 repairs the three seventh-review findings. Read review-round7-remediation.md and its acceptance record. Keep explicit thinking changes, provider-function identity, stable Ollama IDs, cancellation and legacy capsule integrity. R67/R68 browser/publication gates carry forward; only the built-in browser is allowed. R50/U49/R59 remain.


R69/P54 code and exact-source CI are verified (676 backend/79 frontend). The required in-app browser remains unavailable; R67/R68 browser acceptance and the combined R67/R68/R69 preview publication are pending. Preserve v1 capsule validation and strict tool-history mismatch checks.

- R70/P55 repairs the two eighth-review findings. Read review-round8-remediation.md and its acceptance record. Preserve legacy canonical prefixes, undispatched-tool exclusion, per-call consumer ID mappings, raw signatures and strict function checks. R67/R68 browser acceptance and combined preview publication remain pending; built-in browser only. R50/U49/R59 remain.

R70/P55 code and exact-source CI are verified (710 backend/79 frontend). The required in-app browser remains unavailable; R67/R68 interaction checks and combined R67-R70 preview publication are pending. Preserve raw signatures and exact per-call canonical/provider ID correspondence.

- R71/P56 repairs four ninth-review findings. Read review-round9-remediation.md and its acceptance record. Preserve single-login revocation, independent sessions, signed block order across all protocols, witnessed tag projection and Arena response identity. The built-in browser is connected again; complete carried-forward R67/R68 interactions before the combined R67-R71 preview update. R50/U49/R59 remain.

- R71/P56 and the combined R67-R71 batch are verified on isolated preview. The required built-in browser is connected and carried-forward interactions passed. Read review-round9-remediation-acceptance.json for exact source/CI/image/GitOps and remaining limits. Earlier pending entries are historical. Keep R50/U49/R59.

- R72/P57 repairs four tenth-review boundaries. Read review-round10-remediation.md and its acceptance record. Keep actual consumer projections, per-call stream invalidation, all-protocol tool IDs and sparse Responses indexes. R71 preview is already accepted; R72 checks and delivery are separate. R50/U49/R59 and built-in browser only remain.
