/** Display-only observations. Never edits history or constructs provider requests. */
export const contextEstimate = (value: unknown): number => {
	if (value == null) return 0;
	const text =
		typeof value === 'string'
			? value
			: JSON.stringify(value, (key, item) =>
					['oi_native', 'encrypted_content', 'signature', 'thoughtSignature'].includes(key)
						? undefined
						: item
				);
	return Math.ceil((text ?? '').replace(/data:[^;,\s]+;base64,[A-Za-z0-9+/=]+/g, '').length / 4);
};
export const positiveLimit = (value: unknown): number | null =>
	typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
export function contextBudget(capabilities: any, reserve: unknown) {
	const input = positiveLimit(capabilities?.input_limit);
	const window = positiveLimit(capabilities?.context_window);
	const output = positiveLimit(reserve);
	const shared = window == null ? null : Math.max(0, window - (output ?? 0));
	return {
		input,
		window,
		output,
		effective: input == null ? shared : shared == null ? input : Math.min(input, shared)
	};
}
export function branchMessages(history: any, currentId: string | null) {
	const seen = new Set<string>(),
		messages: any[] = [];
	let id = currentId;
	while (id && !seen.has(id)) {
		seen.add(id);
		const message = history.messages?.[id];
		if (!message) break;
		messages.unshift(message);
		id = message.parentId;
	}
	return messages;
}
export function contextProjection(
	history: any,
	currentId: string | null,
	draft = '',
	system = '',
	compacted = false
) {
	let messages = branchMessages(history, currentId);
	const checkpoint = messages.findLastIndex(
		(m) =>
			typeof (m.contextSummary ?? m.context_summary) === 'string' &&
			(m.contextSummary ?? m.context_summary).trim()
	);
	const summary =
		checkpoint < 0
			? null
			: (messages[checkpoint].contextSummary ?? messages[checkpoint].context_summary);
	if (compacted && checkpoint >= 0) messages = messages.slice(checkpoint);
	const summaryTokens = compacted && summary ? contextEstimate(summary) : 0;
	const messageTokens = messages.reduce(
		(sum, m) => sum + contextEstimate(m.output?.length ? m.output : m.content) + 4,
		0
	);
	const draftTokens = contextEstimate(draft);
	const count = contextEstimate(system) + summaryTokens + messageTokens + draftTokens;
	const turns = messages.filter((m) => m.role === 'user').length;
	return {
		messages,
		summary,
		summaryTokens,
		messageTokens,
		draftTokens,
		count,
		averageTurn: turns > 0 ? messageTokens / turns : null,
		mediaUnknown: messages.some(
			(m) =>
				m.files?.length ||
				(Array.isArray(m.content) &&
					m.content.some((p: any) => !['text', 'input_text', 'output_text'].includes(p.type)))
		)
	};
}
export function tokenSubtotal(calls: any[], key: string) {
	calls = [...new Map(calls.map((call) => [call.id, call])).values()];
	const values = calls
		.map((call) => call.usage?.[key])
		.filter((value) => typeof value === 'number' && Number.isFinite(value));
	return {
		value: values.length ? values.reduce((a, b) => a + b, 0) : null,
		unknown: calls.length - values.length
	};
}

/** Tool definitions are not chat messages. Reuse their last observed estimate, explicitly labelled. */
export function contextOccupancy(textTokens: number, context: any) {
	const raw = context?.categories?.tool_definitions;
	const tools = typeof raw === 'number' && Number.isFinite(raw) && raw >= 0 ? raw : null;
	return { tools, total: textTokens + (tools ?? 0) };
}
