import { describe, expect, it } from 'vitest';
import { modelRanks } from './connectionManagement';

describe('connection model ordering', () => {
	it('groups enabled first and freezes order until an explicit refresh', () => {
		const models = [
			{ id: 'a', connection_id: 'c', name: '模型1', enabled: false },
			{ id: 'b', connection_id: 'c', name: '模型10', enabled: true },
			{ id: 'd', connection_id: 'c', name: '模型2', enabled: true },
			{ id: 'e', connection_id: 'other', name: '模型1', enabled: false }
		];
		const initial = modelRanks(models);
		expect([...initial.keys()]).toEqual(['d', 'b', 'a', 'e']);
		models[0].enabled = true;
		models[1].enabled = false;
		expect([...initial.keys()]).toEqual(['d', 'b', 'a', 'e']);
		const reopened = modelRanks(models, initial, 'c');
		expect(reopened.get('a')).toBe(0);
		expect(reopened.get('b')).toBe(2);
		expect(reopened.get('e')).toBe(initial.get('e'));
	});
});
