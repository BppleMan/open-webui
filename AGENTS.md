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
- Stage 6 is verified with documented provider boundaries in docs/customization/stage6-acceptance.json and stage6-release.json. Continue stage 7 cost/archive hardening, then stage 8 staging/GitOps. Do not repeat stages 4–6 or deploy production before remaining release gates. Physical PWA was waived; native IME and direct official-provider/media validation were not claimed. Preserve synthetic/live/deployment evidence boundaries.
