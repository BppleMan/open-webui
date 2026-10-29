import { describe, expect, it } from 'vitest';
import { replaceOutputText } from './outputEditing';

describe('tabbed structured output editing', () => {
	it('retains non-text content, metadata and other tabs without mutating the original', () => {
		const original = [
			{
				type: 'message',
				content: [
					{ type: 'output_text', text: 'old', annotations: ['a'] },
					{ type: 'image', data: 'synthetic' },
					{ type: 'output_text', text: 'tail', custom: 1 }
				]
			},
			{
				type: 'reasoning',
				encrypted_content: 'synthetic-signature',
				summary: [{ type: 'summary_text', text: 'thought', custom: 2 }]
			},
			{ type: 'function_call', call_id: 'synthetic', arguments: '{}' },
			{ type: 'unknown_provider_part', custom: true }
		];
		const body = replaceOutputText(original, 0, 'content', 'edited body', 'output_text');
		const both = replaceOutputText(body, 1, 'summary', 'edited thought', 'summary_text');
		expect(both[0].content).toEqual([
			{ type: 'output_text', text: 'edited body', annotations: ['a'] },
			{ type: 'image', data: 'synthetic' },
			{ type: 'output_text', text: '', custom: 1 }
		]);
		expect(both[1]).toEqual({
			...original[1],
			summary: [{ type: 'summary_text', text: 'edited thought', custom: 2 }]
		});
		expect(both.slice(2)).toEqual(original.slice(2));
		expect(original[0].content?.[0].text).toBe('old');
		expect(original[1].summary?.[0].text).toBe('thought');
	});
	it('adds text to an empty reasoning summary while preserving opaque fields', () => {
		const original = [{ type: 'reasoning', summary: [], encrypted_content: 'synthetic' }];
		expect(replaceOutputText(original, 0, 'summary', 'new', 'summary_text')).toEqual([
			{ ...original[0], summary: [{ type: 'summary_text', text: 'new' }] }
		]);
	});
});
