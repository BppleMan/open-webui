import { describe, expect, it } from 'vitest';
import {
	compactUsage,
	exactUsage,
	hasReadableThoughts,
	uniqueUsageCalls,
	usageMetrics
} from './messageUsage';

describe('message usage presentation', () => {
	it('distinguishes known zero, missing and incomplete aggregates', () => {
		const calls = [
			{ usage: { input_tokens: 10, cache_read_tokens: 0, cache_write_tokens: 5 } },
			{ usage: { input_tokens: 20, cache_write_tokens: 7 } }
		];
		const values = Object.fromEntries(usageMetrics(calls).map((m) => [m.key, m.value]));
		expect(values.input_tokens).toBe(30);
		expect(values.cache_write_tokens).toBe(12);
		expect(values.cache_read_tokens).toBeNull();
		expect(usageMetrics(calls.slice(0, 1)).find((m) => m.key === 'cache_read_tokens')?.value).toBe(
			0
		);
		expect(usageMetrics([]).every((m) => m.value === null)).toBe(true);
	});
	it('deduplicates repeated call snapshots before token and cost presentation', () => {
		const calls = uniqueUsageCalls([
			{ id: 'a', usage: { input_tokens: 10 }, actual_usd: '0.03' },
			{ id: 'b', usage: { input_tokens: 20 }, actual_usd: '0.04' },
			{ id: 'a', usage: { input_tokens: 12 }, actual_usd: '0.03' }
		]);
		expect(calls.map((c) => c.actual_usd)).toEqual(['0.03', '0.04']);
		expect(usageMetrics(calls).find((m) => m.key === 'input_tokens')?.value).toBe(32);
	});
	it('keeps compact counts separate from exact values and unknown labels', () => {
		expect(compactUsage(12400)).toBe('12.4K');
		expect(exactUsage(12400)).toBe((12400).toLocaleString());
		expect(compactUsage(0)).toBe('0');
		expect(compactUsage(null)).toBe('—');
		expect(exactUsage(null)).toBe('未知');
	});
	it('does not mistake opaque reasoning signatures for displayable thoughts', () => {
		expect(
			hasReadableThoughts([{ type: 'reasoning', summary: [], encrypted_content: 'synthetic' }])
		).toBe(false);
		expect(
			hasReadableThoughts([
				{ type: 'reasoning', summary: [], content: [{ type: 'text', text: 'synthetic thought' }] }
			])
		).toBe(true);
		expect(
			hasReadableThoughts([{ type: 'message', content: [{ type: 'output_text', text: 'answer' }] }])
		).toBe(false);
		expect(hasReadableThoughts(undefined)).toBeNull();
	});
});
