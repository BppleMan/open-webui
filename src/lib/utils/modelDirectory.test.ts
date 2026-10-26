import { beforeEach, describe, expect, it } from 'vitest';
import {
	groupModels,
	chatCapable,
	capableModels,
	modelConnection,
	modelLabel,
	resolveModelReference,
	preserveModelReferences,
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
	it('keeps specialized models out of chat while retaining unknown legacy and explicit mixed capability models', () => {
		const entries = [
			{ id: 'embedding', capabilities: { embedding: true } },
			{ id: 'mixed', capabilities: { image_generation: true, chat: true } },
			{ id: 'legacy' },
			{
				id: 'disabled',
				capabilities: { chat: true },
				info: { meta: { capabilities: { chat: false } } }
			}
		];
		expect(entries.filter(chatCapable).map((model) => model.id)).toEqual(['mixed', 'legacy']);
		expect(capableModels(entries, 'chat').map((model) => model.id)).toEqual(['mixed', 'legacy']);
		expect(capableModels(entries, 'embedding').map((model) => model.id)).toEqual(['embedding']);
	});
	it('does not mistake workspace image-tool toggles for image endpoint capability', () => {
		const model = {
			id: 'legacy-chat',
			info: { meta: { capabilities: { image_generation: true } } }
		};
		expect(chatCapable(model)).toBe(true);
		expect(capableModels([model], 'image_generation')).toEqual([]);
	});
	it('keeps duplicates separate and resolves only unique aliases', () => {
		expect(groupModels([a, b]).map((g) => g.connection.id)).toEqual(['a', 'b']);
		expect(resolveModelReference([a, b], 'ambiguous')).toBeNull();
		expect(modelConnection('ambiguous').id).toBe('unresolved');
		expect(modelConnection('old-a').id).toBe('a');
		expect(modelLabel('oi:b')).toBe('Connection B / Same model');
	});
	it('keeps saved unavailable and ambiguous bindings rather than choosing another connection', () => {
		expect(
			preserveModelReferences([a, b], ['missing', 'ambiguous', 'old-a', 'oi:b', 'oi:b'])
		).toEqual(['missing', 'ambiguous', 'oi:a', 'oi:b', 'oi:b']);
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
