import { expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';

const source = readFileSync('src/lib/components/chat/Chat.svelte', 'utf8');
const branch = source
	.split("} else if (type === 'chat:tasks:cancel') {")[1]
	.split("} else if (type === 'chat:message:delta'")[0];
const cancel = new Function(
	'ctx',
	`return (async () => {
		let {browserInteractions,resolveBrowserInteraction,message,event,bridgeCancellations,
		clearContextCompaction,data,history,taskIds,processNextInQueue,$chatId}=ctx;
		${branch}
		return {message,history,taskIds};
	})();`
);

it.each([false, true])(
	'cancel snapshot updates native state with partial output (shared=%s)',
	async (shared) => {
		const message = { parentId: 'user', oi_native_state: 'old', output: [{ text: 'old' }] };
		const queue = vi.fn();
		const ctx = {
			message,
			data: { output: [{ text: 'partial' }], oi_native_state: 'authenticated-partial-state' },
			event: { message_id: 'reply', shared },
			history: {
				currentId: 'reply',
				messages: { user: { childrenIds: ['reply'] }, reply: message }
			},
			browserInteractions: [],
			resolveBrowserInteraction: vi.fn(),
			bridgeCancellations: new Map(),
			clearContextCompaction: vi.fn(),
			processNextInQueue: queue,
			taskIds: ['task'],
			$chatId: 'temporary'
		};
		const result = await cancel(ctx);
		expect(result.message.output).toEqual(ctx.data.output);
		expect(result.message.oi_native_state).toBe(ctx.data.oi_native_state);
		expect(result.message.done).toBe(true);
		expect(result.message.bridgeCancelled).toBe(true);
		expect(queue).toHaveBeenCalledTimes(shared ? 0 : 1);
	}
);
