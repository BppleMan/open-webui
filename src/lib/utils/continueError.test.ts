import { it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
const source = readFileSync('src/lib/components/chat/Chat.svelte', 'utf8');
const completion = source.match(
	/const chatCompletionEventHandler = (async \(data, message, chatId, shared = false\) => \{[\s\S]*?\n\t\});/
)![1];
const start = source.match(/const continueResponse = (async \(message\) => \{[\s\S]*?\n\t\});/)![1];

it.each(['success', 'failure', 'partial'])(
	'applies the latest attempt error for %s',
	async (result) => {
		const message: any = {
			id: 'reply',
			model: 'model',
			error: { content: 'old error' },
			content: 'partial'
		};
		const env: any = {
			history: { messages: { reply: message } },
			$contextCompactions: {},
			clearContextCompaction: vi.fn(),
			handleOpenAIError: (error: any, m: any) => {
				m.error = { content: error };
			},
			$settings: { responseAutoCopy: true },
			$showCallOverlay: false,
			bridge: null,
			copyToClipboard: vi.fn(),
			getOutputText: () => 'new answer',
			removeAllDetails: (v: string) => v,
			dispatchCallOverlayAudio: vi.fn(),
			eventTarget: { dispatchEvent: vi.fn() },
			tick: async () => {},
			shouldAutoScrollResponse: () => false,
			scrollToBottom: vi.fn(),
			chatCompletedHandler: vi.fn(),
			createMessagesList: () => [],
			processNextInQueue: vi.fn()
		};
		const run = new Function('env', `with(env) {return (${completion})}`)(env);
		const data =
			result === 'partial' ? {} : { done: true, error: result === 'failure' ? 'new error' : null };
		await run(data, message, 'chat');
		if (result === 'success') {
			expect(message.error).toBeNull();
			expect(env.copyToClipboard).toHaveBeenCalledOnce();
			expect(env.chatCompletedHandler).toHaveBeenCalledOnce();
			expect(env.eventTarget.dispatchEvent).toHaveBeenCalledOnce();
		} else {
			expect(message.error.content).toBe(result === 'failure' ? 'new error' : 'old error');
			expect(env.copyToClipboard).not.toHaveBeenCalled();
			expect(env.chatCompletedHandler).not.toHaveBeenCalled();
		}
	}
);

it('clears the old warning before Continue sends the next attempt', async () => {
	const message = { id: 'reply', model: 'model', done: true, error: { content: 'old' } };
	const send = vi.fn(async () => {
		expect(message.error).toBeNull();
		expect(message.done).toBe(false);
	});
	const env = {
		$chatId: 'chat',
		history: { messages: { reply: message } },
		tick: async () => {},
		$models: [{ id: 'model' }],
		sendMessageSocket: send,
		createMessagesList: () => []
	};
	await new Function('env', `with(env) {return (${start})}`)(env)(message);
	expect(send).toHaveBeenCalledOnce();
});
