/** Stable per-open ordering: checkbox saves reuse these ranks until an explicit refresh. */
export function modelRanks(
	models: any[],
	previous = new Map<string, number>(),
	connectionId?: string
) {
	const ranks = connectionId ? new Map(previous) : new Map<string, number>();
	const groups = new Map<string, any[]>();
	for (const model of models) {
		if (connectionId && model.connection_id !== connectionId) continue;
		const group = groups.get(model.connection_id) ?? [];
		group.push(model);
		groups.set(model.connection_id, group);
	}
	const collator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' });
	for (const group of groups.values()) {
		group.sort(
			(a, b) =>
				Number(b.enabled) - Number(a.enabled) ||
				collator.compare(a.name, b.name) ||
				a.id.localeCompare(b.id)
		);
		group.forEach((model, index) => ranks.set(model.id, index));
	}
	return ranks;
}
