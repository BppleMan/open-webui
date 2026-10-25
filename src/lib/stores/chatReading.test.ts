import { describe, it, expect } from 'vitest';
import { get } from 'svelte/store';
import { createReadingStore } from './chatReading';

function fixture() {
	let server: any = { revision: 0, data: {} };
	const requests: any[] = [];
	const request = async (path: string, method = 'GET', body?: any) => {
		requests.push({ path, method });
		if (method === 'PUT') {
			if (body.revision !== server.revision)
				throw Object.assign(new Error('Conflict'), { status: 409 });
			server = { revision: server.revision + 1, data: structuredClone(body.data) };
		}
		return structuredClone(server);
	};
	return {
		store: createReadingStore('chat', request),
		requests,
		remote: () => {
			server = {
				revision: server.revision + 1,
				data: { branch_names: { remote: 'Other device' } }
			};
		}
	};
}

describe('reading metadata', () => {
	it('serializes bookmark, reading position and branch-name edits without losing other fields', async () => {
		const h = fixture();
		await Promise.all([
			h.store.change((data) => ({ ...data, bookmarks: { m: { label: 'scene', created_at: 1 } } })),
			h.store.change((data) => ({
				...data,
				read_position: { message_id: 'm', offset: 40, updated_at: 2 }
			})),
			h.store.change((data) => ({ ...data, branch_names: { m: 'chosen' } }))
		]);
		expect(get(h.store).data).toEqual({
			bookmarks: { m: { label: 'scene', created_at: 1 } },
			read_position: { message_id: 'm', offset: 40, updated_at: 2 },
			branch_names: { m: 'chosen' }
		});
		expect(h.requests.every((r) => r.path === '/chats/chat/annotations')).toBe(true);
	});
	it('reports a remote edit and reloads it instead of overwriting it', async () => {
		const h = fixture();
		await h.store.load();
		h.remote();
		await expect(
			h.store.change((data) => ({ ...data, branch_names: { local: 'mine' } }))
		).rejects.toThrow('另一端');
		expect(get(h.store).data.branch_names).toEqual({ remote: 'Other device' });
		await h.store.change((data) => ({
			...data,
			branch_names: { ...data.branch_names, local: 'mine' }
		}));
		expect(get(h.store).data.branch_names).toEqual({ remote: 'Other device', local: 'mine' });
	});
});
