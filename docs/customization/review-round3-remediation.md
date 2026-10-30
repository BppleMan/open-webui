# Third review remediation — R65 / P50

Seven independently reproduced findings are addressed as one validation batch on upstream v0.12.5. Original review evidence remains historical; implementation, checks and preview publication are separate states.

- **CR3-01**: the temporary save handler makes the chat inert before asynchronous work, blocks duplicate saves, transfers pending editor events until stable, and checks the active user/draft context before retirement. Initial page recovery still works; explicit new chats retain distinct identities.
- **CR3-02**: the stored Chat owns its ledger; Generation owns caller attribution. Runtime rechecks the existing share/write contract, then resolves the ledger owner from the database. Old collaborator-owned ledgers are repaired only against the authoritative Chat owner. Non-chat ledgers keep strict ownership checks. Model permission checks are unchanged. Upstream continue-mode readable shares are collaborative; snapshot shares remain read-only.
- **CR3-03**: shared responses for anonymous/non-owner viewers exclude full native envelopes and temporary capsules in both message representations. Visible content stays available. Owner storage and server-side native replay remain intact.
- **CR3-04**: Ollama usage entering the adapter and ledger is restricted to numeric token/cache/duration measurements. Reply text, thoughts and tool arguments stay outside metering storage.
- **CR3-05**: Gemini modality/cache details map to separate input/output/cache image/audio counters. Incomplete classification, unsupported modalities or missing necessary rates keep estimates unknown. Provider-reported actual amounts remain authoritative.
- **CR3-06**: no-ledger historical archives receive an editable unknown `imported-history` cost baseline; their message projections cannot create duplicate global usage events. Explicit zero and re-export preserve that distinction.
- **CR3-07**: existing frequency/presence penalty controls map to `generationConfig.frequencyPenalty` and `presencePenalty`; known incompatible sampling controls fail before dispatch.

The Gemini mappings follow the [GenerateContent reference](https://ai.google.dev/api/generate-content#UsageMetadata) and [GenerationConfig](https://ai.google.dev/api/generate-content#GenerationConfig). Ollama final-frame metering follows the [chat API](https://docs.ollama.com/api/chat).

Tests use synthetic data and loopback providers. No new schema migration is required. There is no retrospective reconstruction or cleanup of historical records in this batch. Production remains unchanged under R50, and the separate R59 real-provider summary gap remains open.
