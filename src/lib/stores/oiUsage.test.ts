import { it, expect, vi, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { usageFor, registerBillingIdentity } from './oiUsage';
afterEach(() => {
	vi.unstubAllGlobals();
});
function setup() {
	vi.stubGlobal('document', { visibilityState: 'visible' });
	vi.stubGlobal('localStorage', { token: 'synthetic' });
}
it('clears transient errors after an unchanged successful 304 without dropping data', async () => {
	setup();
	const payload = { totals: { actual_usd: '0.03' }, calls: [], generations: [], snapshots: [] };
	const fetch = vi
		.fn()
		.mockResolvedValueOnce(Response.json(payload, { headers: { ETag: '"stable"' } }))
		.mockResolvedValueOnce(Response.json({ detail: 'temporary outage' }, { status: 502 }))
		.mockResolvedValueOnce(new Response(null, { status: 304 }));
	vi.stubGlobal('fetch', fetch);
	const usage = usageFor('recovery');
	await usage.refresh();
	await usage.refresh();
	expect(get(usage).error).toBe('temporary outage');
	await usage.refresh();
	expect(get(usage)).toEqual(payload);
	expect(fetch.mock.calls[2][1].headers['If-None-Match']).toBe('"stable"');
});
it('does not apply an old temporary ledger response after the conversation identity changes', async () => {
	setup();
	let resolve: (response: Response) => void = () => {};
	vi.stubGlobal(
		'fetch',
		vi.fn(() => new Promise<Response>((r) => (resolve = r)))
	);
	registerBillingIdentity('local:socket', 'temporary:first');
	const usage = usageFor('local:socket');
	const old = usage.refresh();
	registerBillingIdentity('local:socket', 'temporary:second');
	resolve(
		Response.json({ totals: { actual_usd: '123' }, calls: [], generations: [], snapshots: [] })
	);
	await old;
	expect(get(usage).totals).toBeNull();
});
