# Stage6 — native protocols, reasoning and context

Scope: complete the approved stage6, preserve full RP input by default, no production deployment. Reuse stages4–5 evidence without claiming synthetic tests are live-provider checks.

1. Audit seven protocols: OpenAI Chat/Responses, Anthropic, Gemini GenerateContent/Interactions, Ollama and CPA/AGY. Exercise streaming/nonstream, tool roundtrip, multimodal/history replay, usage interruption, finish reasons, errors and no retry on429. Document native field preservation.
2. Verify native model controls against captured outbound requests: discrete efforts and numeric budgets, unsupported values rejected, identity isolated across connections.
3. Align composer-ring details with pinned LibreChat source: distinguish context capacity/input limit/output reserve, measured versus estimated versus unknown, current branch versus all-call totals, categories/cache/tools/skills/MCP/subagents, warnings/turn estimate and explicit manual compaction state. No implicit summarization, trimming or extra model calls.
4. Use real existing connections with synthetic short prompts where credentials and supported protocol are available. Explicitly record inaccessible/unsupported provider gates; never call a gateway proof of official-provider acceptance or mark an unavailable integration passed.
5. Complete targeted and full regressions, type delta, build, built-in browser/outbound acceptance, public commit/CI/image and three-layer records. Remaining stages7–8 stay separate.
