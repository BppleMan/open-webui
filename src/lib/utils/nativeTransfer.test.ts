import { it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { nativeMessageSnapshot } from './nativeMessageSnapshot';
const source = readFileSync('src/lib/components/chat/Messages.svelte', 'utf8');
const handler = source.slice(
	source.indexOf('\tconst editMessage = async '),
	source.indexOf('\n\tconst actionMessage = async ')
);
const run = (env: any) => new Function('env', `with(env){${handler};return editMessage;}`)(env);
const fixture = () => ({
	id: 'reply',
	role: 'assistant',
	model: 'oi:model',
	parentId: 'user',
	childrenIds: [],
	content: 'original',
	output: [{ type: 'message', content: [{ type: 'output_text', text: 'original' }] }],
	oi_native_state: 'source-capsule'
});
it.each([false, true])(
	'copies temporary output using server-authenticated state; edited=%s',
	async (edited) => {
		const original = fixture(),
			before = structuredClone(original),
			next = structuredClone(original.output);
		if (edited) next[0].content[0].text = 'edited';
		const env: any = {
			history: {
				currentId: 'reply',
				messages: { user: { id: 'user', childrenIds: ['reply'] }, reply: original }
			},
			chatId: 'local:source',
			oiTemporaryId: 'temp',
			selectedModels: ['oi:model'],
			toast: { error: vi.fn() },
			$i18n: { t: (s: string) => s },
			uuidv4: () => 'copy',
			getOutputText: (out: any) => out[0].content[0].text,
			tick: async () => {},
			sendMessage: vi.fn(),
			updateChat: vi.fn(),
			oiApi: vi.fn(async (_path, _method, body) => {
				expect(body.source.id).toBe('reply');
				expect(body.destination.id).toBe('copy');
				expect(body.temporary_id).toBe('temp');
				return {
					oi_native_state: edited ? null : 'rebound-capsule',
					oi_native_invalidated: edited
				};
			})
		};
		await run(env)('reply', { output: next });
		expect(env.history.messages.copy.oi_native_state).toBe(edited ? null : 'rebound-capsule');
		expect(env.history.messages.copy.oi_source_message_id).toBe('reply');
		expect(original).toEqual(before);
		expect(env.sendMessage).not.toHaveBeenCalled();
		expect(env.updateChat).toHaveBeenCalledOnce();
	}
);
it('does not put a delayed copy into a different conversation', async () => {
	const original = fixture();
	let finish!: () => void;
	const pending = new Promise<void>((r) => (finish = r));
	const env: any = {
		history: {
			currentId: 'reply',
			messages: { user: { id: 'user', childrenIds: ['reply'] }, reply: original }
		},
		chatId: 'local:source',
		oiTemporaryId: 'temp',
		selectedModels: ['oi:model'],
		toast: { error: vi.fn() },
		$i18n: { t: (s: string) => s },
		uuidv4: () => 'copy',
		getOutputText: () => 'original',
		tick: async () => {},
		sendMessage: vi.fn(),
		updateChat: vi.fn(),
		oiApi: vi.fn(async () => {
			await pending;
			return { oi_native_state: 'rebound' };
		})
	};
	const task = run(env)('reply', { output: original.output });
	env.history = { currentId: null, messages: {} };
	env.chatId = 'new-chat';
	finish();
	await task;
	expect(env.history.messages).toEqual({});
	expect(env.updateChat).not.toHaveBeenCalled();
});
it('keeps portable model, capsule and nontext fields without trusting raw envelopes', () => {
	const result = nativeMessageSnapshot({
		...fixture(),
		meta: { tag: 'kept' },
		audio: { id: 'audio' },
		refusal: 'kept',
		oi_native: [{ secret: 'raw' }]
	});
	expect(result.model).toBe('oi:model');
	expect(result.oi_native_state).toBe('source-capsule');
	expect(result.audio.id).toBe('audio');
	expect(result.refusal).toBe('kept');
	expect(result).not.toHaveProperty('oi_native');
});
it('waits for the authoritative cancel event after HTTP stop acknowledgement', async () => {
	const side = readFileSync('src/lib/components/chat/ChatTabs/SideChat.svelte', 'utf8');
	const stop = side.match(/const stop = (async \(\) => \{[\s\S]*?\n\t\});/)![1];
	const env: any = {
		sendEpoch: 0,
		cancelInteractions: vi.fn(),
		request: Promise.resolve(),
		id: 'local:side',
		stopTasksByChatId: vi.fn(async () => ({})),
		localStorage: { token: 'synthetic' },
		busy: true,
		disposed: false,
		messages: [{ done: false }],
		getErrorMessage: String,
		error: ''
	};
	await new Function('env', `with(env){return (${stop})}`)(env)();
	expect(env.busy).toBe(true);
	expect(env.messages[0].done).toBe(false);
	expect(env.sendEpoch).toBe(1);
});
