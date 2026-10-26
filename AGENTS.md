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
- Next: stage 5 auth/draft reliability and physical PWA acceptance, then stages 6 protocol/context, 7 costs/archive hardening, 8 staging/GitOps. Stage 4 is verified in docs/customization/stage4-release.json; do not repeat its implementation or treat synthetic evidence as real-provider/PWA acceptance.
