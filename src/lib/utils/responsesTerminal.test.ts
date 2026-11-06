import { expect, it } from 'vitest';
import { applyResponseStreamEvent } from '../components/chat/Messages/structuredOutput';

it.each(['completed', 'incomplete', 'failed'])(
	'keeps prior tool results and %s terminal output',
	(status) => {
		const prior = [
			{ type: 'function_call', id: 'fc', call_id: 'tool', name: 'lookup', arguments: '{}' },
			{
				type: 'function_call_output',
				id: 'result',
				call_id: 'tool',
				output: [{ type: 'input_text', text: 'kept' }]
			}
		];
		const partial = {
			type: 'message',
			id: 'answer',
			content: [{ type: 'output_text', text: 'partial answer' }]
		};
		const original = structuredClone(prior);
		const result = applyResponseStreamEvent(prior, {
			type: `response.${status}`,
			response: { status, output: [partial] }
		});
		expect(result).toEqual([...prior, partial]);
		expect(prior).toEqual(original);
		expect(
			applyResponseStreamEvent(result, {
				type: `response.${status}`,
				response: { status, output: [] }
			})
		).toEqual(result);
	}
);
