// Portable client projection. Raw provider envelopes are transferred only by the authenticated server.
const fields = [
	'id',
	'role',
	'parentId',
	'childrenIds',
	'content',
	'output',
	'model',
	'selectedModelId',
	'meta',
	'files',
	'contextSummary',
	'oi_native_state',
	'oi_native_invalidated',
	'oi_source_message_id',
	'reasoning_content',
	'reasoning',
	'thinking',
	'tool_calls',
	'function_call',
	'reasoning_details',
	'provider_specific_fields',
	'annotations',
	'refusal',
	'audio',
	'images'
];
export const nativeMessageSnapshot = (message: any): any =>
	structuredClone(
		Object.fromEntries(fields.filter((key) => key in message).map((key) => [key, message[key]]))
	);
