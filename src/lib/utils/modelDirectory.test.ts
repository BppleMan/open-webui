import { beforeEach, describe, expect, it } from 'vitest';
import {
	groupModels,
	capableModels,
	modelConnection,
	modelLabel,
	resolveModelReference,
	setModelDirectory
} from './modelDirectory';
const a = {
	id: 'oi:a',
	name: 'Same model',
	connection: { id: 'a', name: 'Connection A' },
	aliases: ['ambiguous', 'old-a']
};
const b = {
	id: 'oi:b',
	name: 'Same model',
	connection: { id: 'b', name: 'Connection B' },
	aliases: ['ambiguous']
};
beforeEach(() => setModelDirectory([a, b]));
describe('connection directory identities', () => {
	it('keeps duplicates separate and resolves only unique aliases', () => {
		expect(groupModels([a, b]).map((g) => g.connection.id)).toEqual(['a', 'b']);
		expect(resolveModelReference([a, b], 'ambiguous')).toBeNull();
		expect(modelConnection('ambiguous').id).toBe('unresolved');
		expect(modelConnection('old-a').id).toBe('a');
		expect(modelLabel('oi:b')).toBe('Connection B / Same model');
	});
	it('groups persisted presets under the concrete base and rejects cycles', () => {
		expect(modelConnection({ id: 'preset', base_model_id: 'oi:a' })).toEqual(a.connection);
		expect(modelConnection({ value: 'oi:a', model: a })).toEqual(a.connection);
		setModelDirectory([
			{ id: 'cycle-a', info: { base_model_id: 'cycle-b' } },
			{ id: 'cycle-b', info: { base_model_id: 'cycle-a' } }
		]);
		expect(modelConnection('cycle-a').id).toBe('unresolved');
	});
	it('respects explicit capability overrides and leaves unknown models out', () => {
		const models = [
			{ id: 'known', capabilities: { vision: true } },
			{
				id: 'disabled',
				capabilities: { vision: true },
				info: { meta: { capabilities: { vision: false } } }
			},
			{ id: 'unknown' }
		];
		expect(capableModels(models, 'vision').map((m) => m.id)).toEqual(['known']);
	});
});
