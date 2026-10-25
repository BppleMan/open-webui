import { writable, get } from 'svelte/store';
import { oiApi } from '$lib/apis/customization';

export type ReadingPosition = { message_id: string; offset?: number; updated_at: number };
export type ReadingData = {
	bookmarks?: Record<string, { label: string; excerpt?: string; created_at: number }>;
	branch_names?: Record<string, string>;
	read_position?: ReadingPosition;
};
const stores = new Map<string, ReturnType<typeof createReadingStore>>();
export function readingFor(userId: string, chatId: string) {
	const key = `${userId}:${chatId}`;
	if (!stores.has(key)) stores.set(key, createReadingStore(chatId));
	return stores.get(key)!;
}
export function createReadingStore(chatId: string, request = oiApi) {
	const state = writable({ revision: 0, data: {} as ReadingData, loaded: false, error: '' });
	let loading: Promise<void> | null = null;
	let writes = Promise.resolve();
	async function load(force = false) {
		if (loading) return loading;
		if (!force && get(state).loaded) return;
		loading = (async () => {
			try {
				const result = await request(`/chats/${chatId}/annotations`);
				state.set({ ...result, loaded: true, error: '' });
			} catch (cause) {
				state.update((s) => ({ ...s, error: String(cause) }));
				throw cause;
			}
		})().finally(() => {
			loading = null;
		});
		return loading;
	}
	function change(edit: (data: ReadingData) => ReadingData) {
		const operation = writes
			.catch(() => {})
			.then(async () => {
				await load();
				const current = get(state);
				try {
					const result = await request(`/chats/${chatId}/annotations`, 'PUT', {
						revision: current.revision,
						data: edit(structuredClone(current.data))
					});
					state.set({ ...result, loaded: true, error: '' });
				} catch (cause: any) {
					if (cause.status === 409) {
						await load(true);
						const error = new Error('阅读标记已在另一端更新，已载入新版本，请重试本次操作');
						state.update((s) => ({ ...s, error: error.message }));
						throw error;
					}
					state.update((s) => ({ ...s, error: String(cause) }));
					throw cause;
				}
			});
		writes = operation;
		return operation;
	}
	return { ...state, load, change };
}
