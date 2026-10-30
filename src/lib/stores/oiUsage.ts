import { writable } from 'svelte/store';
import { oiApi } from '$lib/apis/customization';
export type UsageData = {
	totals: any;
	calls: any[];
	generations: any[];
	snapshots: any[];
	error?: string;
};
const entries = new Map<string, ReturnType<typeof createUsage>>();
const temporaryIds = new Map<string, string>();
export const registerBillingIdentity = (transport: string, stable: string) =>
	temporaryIds.set(transport, stable);
export const billingIdentity = (chatId: string) => temporaryIds.get(chatId) || chatId;
const empty = (): UsageData => ({ totals: null, calls: [], generations: [], snapshots: [] });
function createUsage(chatId: string) {
	let refs = 0,
		timer: ReturnType<typeof setInterval> | undefined,
		pending: Promise<void> | null = null;
	const state = writable<UsageData>(empty());
	let etag: string | undefined;
	const refresh = async () => {
		if (!chatId || pending || document.visibilityState === 'hidden') return;
		pending = (async () => {
			try {
				const result = await oiApi(
					`/chats/${encodeURIComponent(temporaryIds.get(chatId) || chatId)}/usage`,
					'GET',
					undefined,
					{ etag, onEtag: (value) => (etag = value) }
				);
				if (result) state.set(result);
			} catch (e: any) {
				state.update((value) => ({ ...value, error: e.message }));
			}
		})().finally(() => {
			pending = null;
		});
		return pending;
	};
	return {
		subscribe: state.subscribe,
		refresh,
		connect: () => {
			if (++refs === 1) {
				void refresh();
				timer = setInterval(refresh, 5000);
			}
			return () => {
				if (--refs === 0) clearInterval(timer);
			};
		}
	};
}
export function usageFor(chatId: string) {
	if (!entries.has(chatId)) entries.set(chatId, createUsage(chatId));
	return entries.get(chatId)!;
}
// Decimal string addition avoids rounding monetary values through binary floats.
export function addMoney(values: (string | null | undefined)[]): string {
	const present = values.filter((v): v is string => v != null);
	const scale = Math.max(0, ...present.map((v) => (v.split('.')[1] || '').length));
	const result = present
		.reduce((sum, v) => {
			const [whole, fraction = ''] = v.split('.');
			return sum + BigInt(whole + fraction.padEnd(scale, '0'));
		}, 0n)
		.toString()
		.padStart(scale + 1, '0');
	return scale
		? `${result.slice(0, -scale)}.${result.slice(-scale)}`.replace(/\.?0+$/, '') || '0'
		: result;
}
export const dollars = (value: string | undefined | null) => (value == null ? '未知' : '$' + value);
