import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { DraftManager, browserDraftStore, hasDraftInput, type LocalDraft } from './drafts';

const source = readFileSync('src/lib/components/chat/Chat.svelte', 'utf8');
const handler = source.match(/onSaveTempChat=\{(async \(\) => \{[\s\S]*?\n\t+\})\}/)![1];
beforeEach(() => {
	vi.stubGlobal('document', {
		visibilityState: 'visible',
		addEventListener() {},
		removeEventListener() {}
	});
	vi.stubGlobal('window', { addEventListener() {}, removeEventListener() {} });
	const token = 'x.' + btoa(JSON.stringify({ id: 'u' })) + '.x';
	vi.stubGlobal('localStorage', { token, getItem: () => token });
});
afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
});

it.each(['GET', 'PUT', 'navigation'])(
	'retains queued edits during temporary transfer %s and freezes the chat',
	async (waitAt) => {
		const records = new Map<string, LocalDraft>();
		vi.spyOn(browserDraftStore, 'all').mockImplementation(async () => [...records.values()]);
		vi.spyOn(browserDraftStore, 'put').mockImplementation(async (row) => {
			records.set(row.key, structuredClone(row));
		});
		vi.spyOn(browserDraftStore, 'replace').mockImplementation(async (row, consumed) => {
			records.set(row.key, structuredClone(row));
			for (const old of consumed) if (old.key !== row.key) records.delete(old.key);
		});
		vi.spyOn(browserDraftStore, 'remove').mockImplementation(async (key) => {
			records.delete(key);
		});
		let reached!: () => void, release!: () => void;
		const waiting = new Promise<void>((resolve) => (reached = resolve)),
			gate = new Promise<void>((resolve) => (release = resolve));
		let blocked = false,
			server = { revision: 0, payload: {} as any, mutation_id: '' };
		vi.stubGlobal(
			'fetch',
			vi.fn(async (_url, init = {}) => {
				const method = init.method || 'GET';
				if (method === (waitAt === 'navigation' ? 'PUT' : waitAt) && !blocked) {
					blocked = true;
					reached();
					await gate;
				}
				if (method === 'PUT') {
					const form = JSON.parse(init.body);
					server = {
						revision: server.revision + 1,
						payload: form.payload,
						mutation_id: form.mutation_id
					};
				}
				return Response.json(server);
			})
		);
		const manager = new DraftManager({
			userId: 'u',
			temporary: true,
			onState() {},
			onRestore() {},
			onAllocated() {}
		});
		await manager.load();
		let editor = { prompt: 'at-click', files: [], selectedModels: ['model'] };
		await manager.edit(editor);
		const goto = vi.fn(),
			errors = vi.fn();
		const env = {
			savingTemporaryChat: false,
			tick: async () => {},
			$user: { id: 'u' },
			history: { currentId: 'reply', messages: { reply: { id: 'reply' } } },
			createMessagesList: () => [{ role: 'user', content: 'question' }],
			$i18n: { t: (v: string) => v },
			createNewChat: async () => ({ id: 'saved' }),
			selectedModels: ['model'],
			params: {},
			chatVariables: {},
			draftManager: manager,
			getChatInputDraft: () => editor,
			hasDraftInput,
			DraftManager,
			temporaryChatEnabled: { set() {} },
			chatId: { set() {} },
			refreshChatList: async () => {},
			goto,
			toast: { error: errors, success: vi.fn() },
			uuidv4: () => crypto.randomUUID()
		};
		const harness = new Function(
			...Object.keys(env),
			`return {run:(${handler}), busy:()=>savingTemporaryChat, replace:(value)=>draftManager=value};`
		)(...Object.values(env));
		const saving = harness.run();
		await waiting;
		expect(harness.busy()).toBe(true);
		expect(source).toContain('inert={!!initializingDraft || savingTemporaryChat}');
		editor = { ...editor, prompt: 'queued-during-' + waitAt };
		if (waitAt === 'navigation') harness.replace({});
		else await manager.edit(editor);
		release();
		await saving;
		expect(errors).not.toHaveBeenCalled();
		if (waitAt === 'navigation') {
			expect(server.payload.prompt).toBe('at-click');
			expect([...records.values()].some((row) => row.temporary)).toBe(true);
			expect(goto).not.toHaveBeenCalled();
		} else {
			expect(server.payload.prompt).toBe(editor.prompt);
			expect([...records.values()].some((row) => row.temporary)).toBe(false);
			expect(goto).toHaveBeenCalledWith('/c/saved');
		}
		expect(harness.busy()).toBe(false);
		manager.dispose();
	}
);
