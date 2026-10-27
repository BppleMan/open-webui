import { afterEach, beforeEach, it, expect, vi } from 'vitest';
import { get } from 'svelte/store';
import {
	renewSession,
	readCurrentSession,
	mayReconnectSession,
	sessionRecoveryRequired
} from './session';
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

it('uses the new login after an old bootstrap request returns unauthorized', async () => {
	let reject!: (reason: any) => void;
	const lookup = vi
		.fn()
		.mockImplementationOnce(() => new Promise((_r, bad) => (reject = bad)))
		.mockResolvedValueOnce({ id: 'u', token: 'new-login' });
	const loading = readCurrentSession(lookup);
	token = 'new-login';
	reject({ status: 401 });
	expect(await loading).toEqual({ id: 'u', token: 'new-login' });
	expect(lookup.mock.calls.map(([value]) => value)).toEqual(['original', 'new-login']);
	expect(get(sessionRecoveryRequired)).toBe(false);
});
it('coalesces simultaneous renewals in one tab', async () => {
	vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ token: 'renewed' })));
	const [a, b] = await Promise.all([renewSession(), renewSession()]);
	expect(a).toEqual(b);
	expect(fetch).toHaveBeenCalledTimes(1);
});

it('does not reconnect a revoked socket or clear its draft credential', async () => {
	expect(await mayReconnectSession(vi.fn().mockRejectedValue({ status: 401 }))).toBe(false);
	expect(get(sessionRecoveryRequired)).toBe(true);
	expect(token).toBe('original');
});

it('does not treat a failed reconnect probe as logout and permits a later valid session', async () => {
	expect(await mayReconnectSession(vi.fn().mockRejectedValue({ status: 502 }))).toBe(false);
	expect(get(sessionRecoveryRequired)).toBe(false);
	expect(await mayReconnectSession(vi.fn().mockResolvedValue({ id: 'u' }))).toBe(true);
});
