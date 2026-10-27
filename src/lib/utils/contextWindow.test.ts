import { describe, it, expect } from 'vitest';
import { contextBudget, contextProjection, contextOccupancy, tokenSubtotal } from './contextWindow';
describe('context observations', () => {
	it('separates input limit, shared capacity and reserved output', () => {
		expect(contextBudget({ input_limit: 8000, context_window: 10000 }, 3000).effective).toBe(7000);
		expect(contextBudget({}, 3000).effective).toBeNull();
		expect(contextBudget({ context_window: 1000 }, 2000).effective).toBe(0);
	});
	it('recomputes edited branches and draft text without mutating history', () => {
		const history = {
			messages: {
				a: { id: 'a', role: 'user', content: 'first' },
				b: { id: 'b', parentId: 'a', role: 'assistant', content: 'reply' },
				c: { id: 'c', parentId: 'a', role: 'assistant', content: 'different branch' }
			}
		};
		const before = JSON.stringify(history);
		const b = contextProjection(history, 'b', 'new draft');
		const c = contextProjection(history, 'c');
		expect(b.messages.map((m) => m.id)).toEqual(['a', 'b']);
		expect(c.messages.map((m) => m.id)).toEqual(['a', 'c']);
		expect(JSON.stringify(history)).toBe(before);
		expect(b.draftTokens).toBeGreaterThan(0);
		history.messages.b.content = 'long edited reply'.repeat(40);
		expect(contextProjection(history, 'b').count).toBeGreaterThan(b.count);
	});
	it('ignores stored summaries unless compaction was explicitly enabled', () => {
		const h = {
			messages: {
				a: { id: 'a', role: 'user', content: 'original history'.repeat(50) },
				b: {
					id: 'b',
					parentId: 'a',
					role: 'assistant',
					content: 'recent',
					contextSummary: 'summary'
				}
			}
		};
		expect(contextProjection(h, 'b').messages).toHaveLength(2);
		expect(contextProjection(h, 'b', '', '', true).messages).toHaveLength(1);
	});
	it('deduplicates usage and does not treat unknown as zero or add cache twice', () => {
		const a = { id: 'a', usage: { input_tokens: 100, cache_read_tokens: 80 } },
			b = { id: 'b', usage: {} };
		expect(tokenSubtotal([a, a, b], 'input_tokens')).toEqual({ value: 100, unknown: 1 });
		expect(tokenSubtotal([b], 'input_tokens').value).toBeNull();
	});
});

it('counts observed tool definitions once without duplicating history or cache', () => {
	expect(
		contextOccupancy(100, {
			categories: { tool_definitions: 6300, tool_results: 1000 },
			cache_read_tokens: 80
		})
	).toEqual({ tools: 6300, total: 6400 });
	expect(contextOccupancy(100, null)).toEqual({ tools: null, total: 100 });
});
