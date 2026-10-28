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
