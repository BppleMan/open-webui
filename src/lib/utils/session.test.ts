import { afterEach, beforeEach, it, expect, vi } from 'vitest';
import { get } from 'svelte/store';
import { renewSession, sessionRecoveryRequired } from './session';
let token: string | null;
beforeEach(() => {
	token = 'original';
	sessionRecoveryRequired.set(false);
	vi.stubGlobal('localStorage', {
		getItem: () => token,
		setItem: (_key: string, value: string) => {
			token = value;
		}
	});
});
afterEach(() => {
	vi.unstubAllGlobals();
});
it('does not replace a newer login when an older refresh finishes', async () => {
	let resolve!: (value: Response) => void;
	vi.stubGlobal(
		'fetch',
		vi.fn(
			() =>
				new Promise<Response>((done) => {
					resolve = done;
				})
		)
	);
	const refreshing = renewSession();
	token = 'new-login';
	resolve(Response.json({ token: 'old-session-renewed' }));
	await refreshing;
	expect(token).toBe('new-login');
	expect(get(sessionRecoveryRequired)).toBe(false);
});
it('retains credentials after transport and gateway failures', async () => {
	vi.stubGlobal(
		'fetch',
		vi
			.fn()
			.mockRejectedValueOnce(new Error('Offline'))
			.mockResolvedValueOnce(new Response('', { status: 502 }))
	);
	await renewSession();
	await renewSession();
	expect(token).toBe('original');
	expect(get(sessionRecoveryRequired)).toBe(false);
});
it('opens recovery on a genuine unauthorized refresh without clearing the token', async () => {
	vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 401 })));
	await renewSession();
	expect(token).toBe('original');
	expect(get(sessionRecoveryRequired)).toBe(true);
});
