import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { DraftManager, restoreDraftUploads, createQueuedDraft, type LocalDraft } from './drafts';

beforeEach(() => {
	vi.useFakeTimers();
	vi.stubGlobal('localStorage', { getItem: () => 'x.' + btoa(JSON.stringify({ id: 'u' })) + '.x' });
	vi.stubGlobal('document', { visibilityState: 'visible' });
});
afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

function harness(temporary = false, chatId: string | undefined = 'chat') {
	let server = { revision: 0, payload: {} as any, mutation_id: '' };
	let offline = false,
		uncertain = false;
	const local = new Map<string, LocalDraft>(),
		writes: any[] = [],
		restore = vi.fn();
	const request = vi.fn(async (url: string, init: RequestInit = {}) => {
		if (offline) throw new Error('Offline');
		if (url.endsWith('/chats/drafts')) return Response.json({});
		const form = init.body
			? JSON.parse(init.body as string)
			: {
					revision: Number(new URL(url, 'http://test').searchParams.get('revision')),
					mutation_id: new URL(url, 'http://test').searchParams.get('mutation_id'),
					payload: {}
				};
		if (url.endsWith('/acknowledge')) {
			writes.push({ ...form, method: init.method });
			const previous_revision = server.revision;
			if (form.queue_id) {
				const item = server.payload.queue?.find((item: any) => item.id === form.queue_id);
				if (!item) return Response.json({ ...server, previous_revision, applied: false });
				if (item.requestId !== form.request_id)
					return Response.json({ detail: { current: server } }, { status: 409 });
				form.payload = {
					...server.payload,
					queue: server.payload.queue.filter((item: any) => item.id !== form.queue_id)
				};
			} else {
				if (
					server.mutation_id === form.mutation_id ||
					(form.input_version && server.payload._oi_input_version !== form.input_version)
				)
					return Response.json({ ...server, previous_revision, applied: false });
				if (!form.input_version && form.revision !== server.revision)
					return Response.json({ detail: { current: server } }, { status: 409 });
				form.payload = server.payload.queue?.length ? { queue: server.payload.queue } : {};
			}
			server = {
				revision: previous_revision + 1,
				payload: form.payload,
				mutation_id: form.mutation_id
			};
			if (uncertain) {
				uncertain = false;
				throw new Error('Lost response');
			}
			return Response.json({ ...server, previous_revision, applied: true });
		}
		if (init.method === 'PUT' || init.method === 'DELETE') {
			writes.push({ ...form, method: init.method });
			if (server.mutation_id === form.mutation_id) return Response.json(server);
			if (server.revision !== form.revision)
				return Response.json({ detail: { current: server } }, { status: 409 });
			server = {
				revision: server.revision + 1,
				payload: form.payload,
				mutation_id: form.mutation_id
			};
			if (uncertain) {
				uncertain = false;
				throw new Error('Lost response');
			}
		}
		return Response.json(server);
	});
	const create = () =>
		new DraftManager({
			userId: 'u',
			chatId,
			temporary,
			onState: vi.fn(),
			onRestore: restore,
			onAllocated: vi.fn(),
			request: request as any,
			store: {
				all: async () => [...local.values()],
				put: async (record) => {
					local.set(record.key, structuredClone(record));
				},
				remove: async (key) => {
					local.delete(key);
				}
			}
		});
	return {
		manager: create(),
		create,
		request,
		restore,
		local,
		writes,
		server: () => server,
		offline: (value: boolean) => (offline = value),
		uncertain: () => (uncertain = true),
		remoteEdit: () =>
			(server = {
				...server,
				revision: server.revision + 1,
				payload: { prompt: 'remote' },
				mutation_id: 'remote'
			})
	};
}

describe('durable draft orchestration', () => {
	it('retains a long draft and uses the same mutation after a lost response', async () => {
		const h = harness();
		await h.manager.load();
		await h.manager.edit({ prompt: '中文'.repeat(5000) });
		h.uncertain();
		await h.manager.flush();
		await h.manager.flush();
		expect(h.server().payload.prompt.length).toBe(10000);
		expect(h.server().revision).toBe(1);
		expect(h.writes[0].mutation_id).toBe(h.writes[1].mutation_id);
		h.manager.dispose();
	});
	it('keeps new input when an older submission is accepted', async () => {
		const h = harness();
		await h.manager.load();
		await h.manager.edit({ prompt: 'sent' });
		const ticket = await h.manager.prepareSend();
		await h.manager.edit({ prompt: 'next' });
		await h.manager.flush();
		await h.manager.accepted(ticket);
		expect(h.server().payload.prompt).toBe('next');
		expect(h.manager.payload.prompt).toBe('next');
		h.manager.dispose();
	});
	it('persists a pending clear during an outage and retries without resurrecting sent text', async () => {
		const h = harness();
		await h.manager.load();
		await h.manager.edit({ prompt: 'sent' });
		const ticket = await h.manager.prepareSend();
		h.offline(true);
		await h.manager.accepted(ticket);
		expect(h.manager.payload).toEqual({});
		expect([...h.local.values()][0].pendingClear).toBeDefined();
		h.offline(false);
		await h.manager.refresh();
		expect(h.server().payload).toEqual({});
		h.manager.dispose();
	});
	it('temporary drafts never contact the server', async () => {
		const h = harness(true);
		await h.manager.load();
		await h.manager.edit({ prompt: 'local' });
		await h.manager.flush();
		const ticket = await h.manager.prepareSend();
		await h.manager.accepted(ticket);
		expect(h.request).not.toHaveBeenCalled();
		h.manager.dispose();
	});
	it('does not silently overwrite concurrent server edits', async () => {
		const h = harness();
		await h.manager.load();
		await h.manager.edit({ prompt: 'local' });
		h.remoteEdit();
		await h.manager.flush();
		expect(h.server().payload.prompt).toBe('remote');
		expect(h.manager.payload.prompt).toBe('local');
		await expect(h.manager.prepareSend()).rejects.toThrow('冲突');
		h.manager.dispose();
	});
});

const recovered = (prompt = 'old local', key = 'old-session', temporary = false): LocalDraft => ({
	key,
	userId: 'u',
	chatId: 'chat',
	temporary,
	isNew: temporary,
	payload: { prompt },
	revision: 0,
	dirty: true,
	updatedAt: 1,
	mutationId: key
});

describe('durable queued submissions', () => {
	const queueItem = () =>
		createQueuedDraft({
			prompt: 'queued',
			selectedModels: ['oi:a', 'oi:b'],
			params: { temperature: 0.7 },
			selectedToolIds: ['lookup']
		});
	it('restores the exact models, controls and retry identities after reload', async () => {
		const h = harness();
		await h.manager.load();
		const item = queueItem();
		await h.manager.edit({ prompt: 'next', queue: [item] });
		await h.manager.flush();
		h.manager.dispose();
		const reopened = h.create();
		await reopened.load();
		expect(reopened.payload.queue).toEqual([item]);
		expect(reopened.payload.queue[0].responseIds).toHaveLength(2);
		const ticket = await reopened.prepareQueuedSend(item.id);
		expect(ticket.requestId).toBe(item.requestId);
		reopened.dispose();
	});
	it('acknowledges just one queued version while retaining unsynced composer changes', async () => {
		const h = harness();
		await h.manager.load();
		const first = queueItem(),
			second = queueItem();
		await h.manager.edit({ prompt: 'composer', queue: [first, second] });
		const ticket = await h.manager.prepareQueuedSend(first.id);
		await h.manager.edit({ ...h.manager.payload, prompt: 'new typing' });
		await h.manager.accepted(ticket);
		await h.manager.flush();
		expect(h.server().payload.prompt).toBe('new typing');
		expect(h.server().payload.queue).toEqual([second]);
		h.manager.dispose();
	});
	it('recovers a lost queue acknowledgement without resending or resurrecting its text', async () => {
		const h = harness();
		await h.manager.load();
		const item = queueItem();
		await h.manager.edit({ prompt: 'next', queue: [item] });
		const ticket = await h.manager.prepareQueuedSend(item.id);
		h.uncertain();
		await h.manager.accepted(ticket);
		expect([...h.local.values()][0].pendingQueueAcks).toHaveLength(1);
		h.manager.dispose();
		const reopened = h.create();
		await reopened.load();
		expect(reopened.payload.prompt).toBe('next');
		expect(reopened.payload.queue).toEqual([]);
		expect(h.server().revision).toBe(2);
		reopened.dispose();
	});
	it('does not clear text retyped identically after a submission', async () => {
		const h = harness();
		await h.manager.load();
		await h.manager.edit({ prompt: 'same' });
		const ticket = await h.manager.prepareSend();
		await h.manager.edit({ prompt: '' });
		await h.manager.edit({ prompt: 'same' });
		await h.manager.flush();
		await h.manager.accepted(ticket);
		expect(h.server().payload.prompt).toBe('same');
		h.manager.dispose();
	});
	it('clears accepted input after queue-only changes and preserves the queued message', async () => {
		const h = harness();
		await h.manager.load();
		await h.manager.edit({ prompt: 'sent' });
		const ticket = await h.manager.prepareSend();
		const item = queueItem();
		await h.manager.edit({ ...h.manager.payload, queue: [item] });
		await h.manager.flush();
		await h.manager.accepted(ticket);
		expect(h.server().payload).toEqual({ queue: [item] });
		expect(h.manager.payload).toEqual({ queue: [item] });
		h.manager.dispose();
	});
	it('keeps concurrent remote input visible as a conflict when acknowledging a queued message', async () => {
		const h = harness();
		await h.manager.load();
		const item = queueItem();
		await h.manager.edit({ prompt: 'original', queue: [item] });
		const ticket = await h.manager.prepareQueuedSend(item.id);
		await h.manager.edit({ ...h.manager.payload, prompt: 'local change' });
		h.remoteEdit();
		await h.manager.accepted(ticket);
		await expect(h.manager.prepareSend()).rejects.toThrow('冲突');
		expect(h.server().payload.prompt).toBe('remote');
		expect(h.manager.payload.prompt).toBe('local change');
		h.manager.dispose();
	});
	it('persists a temporary queue locally without making server requests', async () => {
		const h = harness(true);
		await h.manager.load();
		const item = queueItem();
		await h.manager.edit({ prompt: 'local next', queue: [item] });
		const ticket = await h.manager.prepareQueuedSend(item.id);
		await h.manager.accepted(ticket);
		h.manager.dispose();
		const reopened = h.create();
		await reopened.load();
		expect(reopened.payload.prompt).toBe('local next');
		expect(reopened.payload.queue).toEqual([]);
		expect(h.request).not.toHaveBeenCalled();
		reopened.dispose();
	});
});

describe('handled recovery candidates', () => {
	it.each(['server', 'local'] as const)(
		'does not revive a handled candidate after choosing %s',
		async (choice) => {
			const h = harness();
			h.local.set('old-session', recovered());
			h.remoteEdit();
			await h.manager.load();
			await h.manager.resolve(choice);
			h.manager.dispose();
			const reopened = h.create();
			await reopened.load();
			expect(reopened.payload.prompt).toBe(choice === 'server' ? 'remote' : 'old local');
			expect(h.local.has('old-session')).toBe(false);
			expect([...h.local.values()].filter((r) => r.dirty)).toHaveLength(0);
			reopened.dispose();
		}
	);
	it('does not revive a sent temporary candidate, while retaining input typed during sending', async () => {
		const h = harness(true, '');
		h.local.set('old-session', recovered('sent', 'old-session', true));
		await h.manager.load();
		const ticket = await h.manager.prepareSend();
		await h.manager.edit({ prompt: 'next' });
		await h.manager.accepted(ticket);
		h.manager.dispose();
		const reopened = h.create();
		await reopened.load();
		expect(reopened.payload.prompt).toBe('next');
		expect(h.local.has('old-session')).toBe(false);
		expect(h.request).not.toHaveBeenCalled();
		const next = await reopened.prepareSend();
		await reopened.accepted(next);
		reopened.dispose();
		const empty = h.create();
		await empty.load();
		expect(empty.payload).toEqual({});
		empty.dispose();
	});
	it('does not delete a concurrent new version of the recovery candidate', async () => {
		const h = harness();
		h.local.set('old-session', recovered());
		h.remoteEdit();
		await h.manager.load();
		h.local.set('old-session', {
			...recovered('other tab new input'),
			mutationId: 'new',
			updatedAt: 2
		});
		await h.manager.resolve('server');
		h.manager.dispose();
		const reopened = h.create();
		await reopened.load();
		expect(reopened.payload.prompt).toBe('other tab new input');
		expect(h.local.has('old-session')).toBe(true);
		reopened.dispose();
	});
});

it('never allocates an ordinary chat for editor initialization or model-only changes', async () => {
	const h = harness(false, '');
	await h.manager.load();
	await h.manager.edit({
		prompt: '\n\n',
		richContent: { type: 'doc', content: [{ type: 'paragraph' }] },
		selectedModels: ['oi:model']
	});
	await h.manager.flush();
	expect(h.request).not.toHaveBeenCalled();
	expect(h.manager.isNew).toBe(true);
	await h.manager.edit({ prompt: '真正输入的内容', selectedModels: ['oi:model'] });
	await h.manager.flush();
	expect(h.request.mock.calls.filter(([url]) => url.endsWith('/chats/drafts'))).toHaveLength(1);
	expect(h.manager.isNew).toBe(false);
	expect(h.server().payload.prompt).toBe('真正输入的内容');
	h.manager.dispose();
});

it('automatically synchronizes dirty input after connectivity returns', async () => {
	const h = harness();
	await h.manager.load();
	h.offline(true);
	await h.manager.edit({ prompt: 'offline work' });
	await h.manager.flush();
	h.offline(false);
	await h.manager.refresh();
	expect(h.server().payload.prompt).toBe('offline work');
	h.manager.dispose();
});

it('does not overwrite typing while the initial server draft is loading', async () => {
	const h = harness();
	h.remoteEdit();
	let release!: () => void;
	const original = h.request.getMockImplementation()!;
	h.request.mockImplementationOnce(async (url: string, init: RequestInit = {}) => {
		await new Promise<void>((r) => (release = r));
		return original(url, init);
	});
	const loading = h.manager.load();
	await vi.waitFor(() => expect(release).toBeTypeOf('function'));
	expect(await h.manager.edit({ prompt: 'typed while loading' })).toBe(true);
	release();
	await loading;
	expect(h.manager.payload.prompt).toBe('typed while loading');
	await expect(h.manager.prepareSend()).rejects.toThrow('冲突');
	h.manager.dispose();
});

it('exposes interrupted restored uploads without changing completed files or live uploads', () => {
	const payload = {
		prompt: 'keep text',
		files: [
			{ itemId: 'lost', name: 'lost.txt', status: 'uploading' },
			{ itemId: 'live', status: 'uploading' },
			{ id: 'stored', status: 'uploaded' }
		],
		queue: [{ id: 'q', files: [{ name: 'queued.txt', status: 'uploading' }] }]
	};
	const restored = restoreDraftUploads(payload, new Set(['live']));
	expect(restored.prompt).toBe('keep text');
	expect(restored.files[0].status).toBe('error');
	expect(restored.files[0].name).toBe('lost.txt');
	expect(restored.files[1].status).toBe('uploading');
	expect(restored.files[2]).toEqual(payload.files[2]);
	expect(restored.queue[0].files[0].status).toBe('error');
	expect(payload.files[0].status).toBe('uploading');
});
it('reconnect still exposes remote conflicts rather than overwriting them', async () => {
	const h = harness();
	await h.manager.load();
	h.offline(true);
	await h.manager.edit({ prompt: 'offline' });
	await h.manager.flush();
	h.remoteEdit();
	h.offline(false);
	await h.manager.refresh();
	expect(h.server().payload.prompt).toBe('remote');
	await expect(h.manager.prepareSend()).rejects.toThrow('冲突');
	h.manager.dispose();
});

describe('upstream deferred terminal uploads', () => {
	it('keeps an untransferable pending attachment visible as an error after restoration', () => {
		const payload = {
			prompt: 'Keep my text',
			files: [
				{
					itemId: 'pending-file',
					name: 'draft.txt',
					type: 'filesystem',
					status: 'pending',
					file: {}
				}
			]
		};
		const restored = restoreDraftUploads(payload);
		expect(restored.prompt).toBe('Keep my text');
		expect(restored.files[0].name).toBe('draft.txt');
		expect(restored.files[0].status).toBe('error');
	});
});

it('purges deleted chat drafts only for the signed-in owner', async () => {
	const { purgeLocalDrafts } = await import('./drafts');
	const records: any[] = [
		{ key: 'own', userId: 'u', chatId: 'deleted', temporary: false, isNew: false },
		{ key: 'other', userId: 'v', chatId: 'deleted', temporary: false, isNew: false },
		{ key: 'temp', userId: 'u', chatId: 'temporary', temporary: true, isNew: false },
		{ key: 'new', userId: 'u', chatId: 'unsent', temporary: false, isNew: true }
	];
	const removed: string[] = [];
	await purgeLocalDrafts('x.' + btoa(JSON.stringify({ id: 'u' })) + '.x', ['deleted'], {
		all: async () => records,
		put: async () => {},
		remove: async (key) => {
			removed.push(key);
		}
	});
	expect(removed).toEqual(['own']);
});
