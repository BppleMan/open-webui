import { afterEach, describe, expect, it, vi } from 'vitest';
vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: 'https://oi.invalid/api/v1' }));
vi.mock('$lib/apis/customization', () => ({ oiApi: vi.fn() }));
import { runBrowserCompletion, cancelBrowserCompletion } from './browserConnections';

const data = {
	channel: 'owner:session:call',
	model: { id: 'oi:binding', connection: { id: 'browser-a', transport: 'browser', revision: 3 } },
	protocol: 'openai-chat',
	stream: true,
	url: 'https://provider.invalid/v1/chat/completions',
	form_data: {
		model: 'same-model',
		stream: true,
		messages: [{ role: 'user', content: 'synthetic' }]
	}
};
const config = () =>
	Response.json({
		id: 'browser-a',
		base_url: 'https://provider.invalid',
		protocol: 'openai-chat',
		endpoints: {
			generate: 'https://provider.invalid/v1/chat/completions',
			stream: 'https://provider.invalid/v1/chat/completions'
		},
		headers: { Authorization: 'Bearer synthetic-provider' }
	});
afterEach(() => {
	cancelBrowserCompletion();
	vi.unstubAllGlobals();
});

describe('browser execution boundary', () => {
	it('preserves arbitrary UTF8/SSE chunk boundaries and stable transport routing', async () => {
		vi.stubGlobal('localStorage', { token: 'synthetic-oi' });
		const source =
			'data: {"choices":[{"delta":{"content":"汉字"}}]}\n\ndata: {"usage":{"prompt_tokens":123}}\n\n';
		const bytes = new TextEncoder().encode(source);
		const offset = bytes.indexOf(0xe6) + 1;
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(config())
			.mockResolvedValueOnce(
				new Response(
					new ReadableStream({
						start(controller) {
							controller.enqueue(bytes.slice(0, offset));
							controller.enqueue(bytes.slice(offset, offset + 2));
							controller.enqueue(bytes.slice(offset + 2));
							controller.close();
						}
					}),
					{ headers: { 'content-type': 'text/event-stream' } }
				)
			);
		vi.stubGlobal('fetch', fetch);
		const socket = { emit: vi.fn() },
			ack = vi.fn();
		await runBrowserCompletion(data, socket, ack);
		expect(fetch.mock.calls[0][0]).toBe(
			'https://oi.invalid/api/v1/connections/browser-a/browser-config?revision=3&binding_id=oi%3Abinding'
		);
		expect(fetch.mock.calls[0][1].cache).toBe('no-store');
		expect(fetch.mock.calls[1][0]).toBe('https://provider.invalid/v1/chat/completions');
		expect(JSON.parse(fetch.mock.calls[1][1].body).model).toBe('same-model');
		expect(fetch.mock.calls[1][1].credentials).toBe('omit');
		expect(
			socket.emit.mock.calls
				.filter(([, part]) => typeof part === 'string')
				.map(([, part]) => part)
				.join('')
		).toBe(source);
		expect(socket.emit).toHaveBeenLastCalledWith(data.channel, { done: true });
		expect(ack).toHaveBeenCalledOnce();
	});
	it('passes through429 and Retry-After without retrying', async () => {
		vi.stubGlobal('localStorage', { token: 'synthetic-oi' });
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(config())
			.mockResolvedValueOnce(
				Response.json({ error: 'limited' }, { status: 429, headers: { 'Retry-After': '9' } })
			);
		vi.stubGlobal('fetch', fetch);
		const ack = vi.fn();
		await runBrowserCompletion(data, { emit: vi.fn() }, ack);
		expect(fetch).toHaveBeenCalledTimes(2);
		expect(ack.mock.calls[0][0]).toMatchObject({
			status_code: 429,
			headers: { 'retry-after': '9' }
		});
	});
	it('aborts pending requests when the owner browser disconnects', async () => {
		vi.stubGlobal('localStorage', { token: 'synthetic-oi' });
		let reached!: () => void;
		const ready = new Promise<void>((resolve) => (reached = resolve));
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(config())
			.mockImplementationOnce(
				(_url, { signal }) =>
					new Promise((_resolve, reject) => {
						signal.addEventListener('abort', () => reject(new Error('synthetic abort')));
						reached();
					})
			);
		vi.stubGlobal('fetch', fetch);
		const socket = { emit: vi.fn() },
			ack = vi.fn();
		const request = runBrowserCompletion(data, socket, ack);
		await ready;
		cancelBrowserCompletion();
		await request;
		expect(ack.mock.calls[0][0].status_code).toBe(502);
		expect(socket.emit).toHaveBeenLastCalledWith(data.channel, { done: true });
	});
});

import { browserRequest, discoverBrowserModels, modelPage } from './browserConnections';
import { oiApi } from '$lib/apis/customization';
describe('native browser protocols', () => {
	it('uses explicit streaming for Gemini even without a body stream flag', async () => {
		vi.stubGlobal('localStorage', { token: 'synthetic' });
		const url = 'https://p.test/v1beta/models/m:streamGenerateContent?alt=sse';
		const rpc = { ...data, protocol: 'gemini', url, stream: true, form_data: { contents: [] } };
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(
				Response.json({
					protocol: 'gemini',
					endpoints: { stream: url },
					headers: { 'x-goog-api-key': 'synthetic' }
				})
			)
			.mockResolvedValueOnce(new Response('data: {"usageMetadata":{"promptTokenCount":7}}\n\n'));
		vi.stubGlobal('fetch', fetch);
		const socket = { emit: vi.fn() },
			ack = vi.fn();
		await runBrowserCompletion(rpc, socket, ack);
		expect(
			socket.emit.mock.calls.some(
				([, part]) => typeof part === 'string' && part.includes('promptTokenCount')
			)
		).toBe(true);
		expect(ack.mock.calls[0][0].body).toBeUndefined();
	});
	it('cannot change endpoint or authorization through RPC', () => {
		const c = {
			protocol: data.protocol,
			endpoints: { stream: data.url },
			headers: { Authorization: 'Bearer retained' }
		};
		expect(
			browserRequest(
				{ ...data, protocol_headers: { Authorization: 'wrong', 'x-rp-chat-id': 'stable' } },
				c
			).headers
		).toEqual({
			Authorization: 'Bearer retained',
			'x-rp-chat-id': 'stable',
			'Content-Type': 'application/json'
		});
		expect(() => browserRequest({ ...data, url: 'https://other.test' }, c)).toThrow();
		expect(() => browserRequest({ ...data, protocol: 'anthropic' }, c)).toThrow();
	});
	it('keeps already-forwarded usage when a native stream disconnects', async () => {
		vi.stubGlobal('localStorage', { token: 'synthetic' });
		let step = 0;
		const body = new ReadableStream({
			pull(c) {
				if (step++ === 0)
					c.enqueue(new TextEncoder().encode('data: {"usage":{"input_tokens":123}}\n\n'));
				else c.error(new Error('disconnect'));
			}
		});
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValueOnce(config()).mockResolvedValueOnce(new Response(body))
		);
		const socket = { emit: vi.fn() },
			ack = vi.fn();
		await runBrowserCompletion(data, socket, ack);
		expect(socket.emit.mock.calls[0][1]).toContain('123');
		expect(socket.emit).toHaveBeenCalledWith(data.channel, { error: true });
		expect(ack).toHaveBeenCalledOnce();
	});
	it.each(['anthropic', 'gemini'])(
		'paginates %s before applying any model changes',
		async (protocol) => {
			vi.stubGlobal('localStorage', { token: 'synthetic' });
			vi.mocked(oiApi).mockClear();
			const page1 =
				protocol === 'gemini'
					? { models: [{ name: 'models/a' }], nextPageToken: 'cursor' }
					: { data: [{ id: 'a' }], has_more: true, last_id: 'cursor' };
			const page2 =
				protocol === 'gemini'
					? { models: [{ name: 'models/b' }] }
					: { data: [{ id: 'b' }], has_more: false };
			const fetch = vi
				.fn()
				.mockResolvedValueOnce(
					Response.json({
						protocol,
						revision: 3,
						endpoints: { models: 'https://p.test/models' },
						headers: { Authorization: 'Bearer synthetic' }
					})
				)
				.mockResolvedValueOnce(Response.json(page1))
				.mockResolvedValueOnce(Response.json(page2));
			vi.stubGlobal('fetch', fetch);
			await discoverBrowserModels({ id: 'c', revision: 3 });
			expect(String(fetch.mock.calls[2][0])).toContain(
				protocol === 'gemini' ? 'pageToken=cursor' : 'after_id=cursor'
			);
			expect(oiApi).toHaveBeenCalledOnce();
			expect((vi.mocked(oiApi).mock.calls[0][2] as { models: unknown[] }).models).toHaveLength(2);
		}
	);
	it('does not apply a partial discovery on repeated pagination', async () => {
		vi.stubGlobal('localStorage', { token: 'synthetic' });
		vi.mocked(oiApi).mockClear();
		const body = { data: [{ id: 'a' }], has_more: true, last_id: 'same' };
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValueOnce(
					Response.json({
						protocol: 'anthropic',
						endpoints: { models: 'https://p.test/models' },
						headers: {}
					})
				)
				.mockImplementation(() => Promise.resolve(Response.json(body)))
		);
		await expect(discoverBrowserModels({ id: 'c' })).rejects.toThrow('分页重复');
		expect(oiApi).not.toHaveBeenCalled();
		expect(() => modelPage({ data: [], has_more: true }, 'anthropic')).toThrow();
	});
});
