# Stage 6 protocol and context acceptance

## Native transport contract

| Protocol | JSON / streaming | Two tool follow-ups | Images / history | Usage / endings |
|---|---|---|---|---|
| OpenAI Chat | HTTP fixture and existing gateway | Real local tool execution | Image URL + native reasoning_content/message replay | Null-first content regression; cumulative usage; original finish_reason |
| OpenAI Responses | HTTP fixture and existing gateway | Real local tool execution | input_image; output items and encrypted reasoning | Preserve completed output items on interruption; failed/incomplete retained |
| Claude Messages | HTTP fixture and existing gateway | Real local tool execution | image blocks; thinking/signature blocks | message_start usage survives interruption; max_tokens preserved |
| Gemini GenerateContent | HTTP fixture and existing gateway | Real local tool execution | inlineData; native parts/signatures | tool association does not add IDs to signed parts; MAX_TOKENS preserved |
| Gemini Interactions | HTTP fixture and existing gateway | Real local tool execution | image/audio/document native mapping; steps/signature replay | incomplete and failed-with-usage handled |
| Ollama | HTTP fixture (no live model configured) | Real local tool execution | images + native thinking/message replay | indexed streamed tool calls; root think control retained |
| CPA/AGY | HTTP fixture and existing gateway | Real local tool execution | Gemini native parts/extensions retained | stable RP identity; no automatic 429 retry; finish provenance explicit |

All seven tool-loop cases execute the tool function and check two outbound follow-ups, ledger attribution and immutable native envelopes. Input mapping tests cover each native image shape; audio/documents are additional Interactions mappings. Tests do not claim every provider supports every media modality. Provider refusals or unsupported capabilities remain errors, never converted to fabricated success.

Native signed/opaque content is stored separately from editable display text. Incomplete streams retain observed native blocks but set replayable=false. Edited/cross-binding history remains subject to earlier replay guards. Background summary/title tasks cannot overwrite the original answer envelope. Empty manual summaries fail without replacing history with truncated text.

## Reasoning and context

Every protocol has captured HTTP evidence for its native reasoning field. Numeric budgets are range checked and captured separately; unknown defaults do not select the first level. A built-in-browser slider action was followed by a provider capture showing reasoning_effort=high. CPA discovery now exposes declared thinkingSupport only in private RP mode. No model-name guesses or arbitrary default reasoning levels.

The composer ring opens a popup using existing OI components. Primary occupancy combines current branch/draft text estimate with the most recent tool-definition estimate (labelled as such); it never substitutes old actual input tokens for a new draft. Current input limit, shared capacity and reserved output are separate. Actual last input, cache read/write, branch/all-call totals, tools, media/MCP/skills/subagent unknowns, remaining turns and explicit compaction state remain distinct. Cache is an input subset, not extra occupancy. Unknown limits produce no invented percentage; zero available input shows a full warning ring. Tool changes/media/protocol overhead remain an explicit estimation limitation.

The field inventory was compared against LibreChat commit a378657583567310c841d652b0f6b00c83e2bd6b TokenUsage components. Optional balance/Langfuse actions are not fabricated without a configured integration. Detail sections are collapsible, constrained and scrollable on small screens. Observing usage makes no model requests.

Manual compaction is disabled by default. Explicitly enabling it and invoking the existing action creates one attributed summary call, retains original messages/native envelopes, and applies that checkpoint without enabling future automatic compaction. Disabling compaction restores full history. HTTP acceptance sets a threshold of 1 to prove no unsolicited summary call is triggered.

## Evidence boundaries

- Python unit/regression fixtures and isolated real OI HTTP-to-provider tests are separate evidence.
- Live short-text probes exercised six protocols through an existing CPA gateway. This is not direct official OpenAI/Anthropic/Google validation. Ollama has no configured live model. Real tool/multimodal calls use synthetic local fixtures; supplier-specific media limits remain a release compatibility check.
- CPA companion metadata is tested locally and in its own CI; the existing live gateway still reports completion source as unspecified until a later GitOps deployment. No production configuration changed.
- Desktop/mobile acceptance uses the built-in browser; physical PWA remains waived. Browser automation stalls are tool failures, not app latency measurements.
- Raw captures, account material and environment addresses remain private. Public fixtures contain synthetic data only.

## Reproduce

Run backend test/oi, frontend Vitest, scripts/oi/check-types.py and production build. scripts/oi/stage6-acceptance.py starts fresh loopback OI/provider processes with disposable SQLite; CI supplies the existing isolated pgvector service for the slim runtime. It asserts protocol controls/ledger plus explicit compaction/history restoration. It never targets production or calls a paid provider.

## Reference schemas

- https://developers.openai.com/api/docs/guides/reasoning#preserve-reasoning-without-stored-responses
- https://platform.claude.com/docs/en/build-with-claude/streaming
- https://ai.google.dev/gemini-api/docs/streaming
- https://ai.google.dev/api/interactions-api

Release revisions, exact check results and immutable image references are recorded in stage6-release.json. Stages 7–8 remain separate gates.
