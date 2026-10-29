# OI customization

Read docs/customization/requirements.md, prototype.md, implementation.md and features.json before changes. The user has approved implementation of this contract. Keep feature implementation, verification and deployment statuses separate.

- Base: upstream v0.12.3. Preserve upstream licensing and branding.
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
