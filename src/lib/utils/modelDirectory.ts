export type ConnectionIdentity = {
	id: string;
	name: string;
	protocol?: string;
	transport?: 'server' | 'browser';
};
export type DirectoryModel = {
	id: string;
	name?: string;
	connection?: ConnectionIdentity;
	aliases?: string[];
	capabilities?: Record<string, any>;
	[key: string]: any;
};

let directory = new Map<string, DirectoryModel>();
let historical: DirectoryModel[] = [];
export const setHistoricalModelDirectory = (models: DirectoryModel[]) => {
	historical = models;
};
export const modelIdentity = (id: string): DirectoryModel | undefined => directory.get(id);
export const setModelDirectory = (models: DirectoryModel[]) => {
	directory = new Map([...historical, ...models].map((m) => [m.id, m]));
};

export function modelConnection(model: any): ConnectionIdentity {
	model = model?.model && typeof model.model === 'object' ? model.model : model;
	if (typeof model === 'string') model = { id: model };
	const seen = new Set<string>();
	while (model) {
		if (model.connection?.id) return model.connection;
		if (model.connection_id && model.connection_name)
			return { id: model.connection_id, name: model.connection_name, protocol: model.protocol };
		if (model.owned_by === 'arena' || model.arena)
			return { id: 'composite:arena', name: '组合模型 · Arena' };
		if (model.pipe)
			return {
				id: `extension:${model.pipe.id || model.id}`,
				name: '扩展 · ' + (model.pipe.name || model.pipe.id || model.id)
			};
		if (seen.has(model.id)) break;
		seen.add(model.id);
		const known = directory.get(model.id);
		if (known?.connection) return known.connection;
		const base = model.base_model_id || model.info?.base_model_id || known?.info?.base_model_id;
		const reference = base || model.id;
		const resolved = resolveModelReference([...directory.values()], reference);
		model = resolved ? directory.get(resolved) : undefined;
	}
	return { id: 'unresolved', name: '待映射连接' };
}

export function groupModels<T>(items: T[]): { connection: ConnectionIdentity; items: T[] }[] {
	const groups = new Map<string, { connection: ConnectionIdentity; items: T[] }>();
	for (const item of items) {
		const connection = modelConnection(item);
		if (!groups.has(connection.id)) groups.set(connection.id, { connection, items: [] });
		groups.get(connection.id)!.items.push(item);
	}
	return [...groups.values()];
}
export const sortByConnection = <T>(items: T[]) => groupModels(items).flatMap((g) => g.items);
export const modelLabel = (model: any) => {
	const item = typeof model === 'string' ? directory.get(model) || { id: model } : model;
	const connection = modelConnection(item);
	return `${connection.name}${connection.transport === 'browser' ? ' · 浏览器直连' : ''} / ${item?.name || item?.id || ''}`;
};
export const modelMatches = (model: DirectoryModel, query: string) =>
	`${modelConnection(model).name} ${model.name || ''} ${model.id} ${model.upstream_id || ''}`
		.toLocaleLowerCase()
		.includes(query.toLocaleLowerCase());
const specializedCapabilities = [
	'embedding',
	'rerank',
	'tts',
	'stt',
	'realtime',
	'image_generation',
	'image_edit'
];
export function chatCapable(model: DirectoryModel): boolean {
	const caps = model.capabilities || {};
	if (model.info?.meta?.capabilities?.chat === false) return false;
	if (typeof caps.chat === 'boolean') return caps.chat;
	// Legacy/unknown chat models remain manually selectable; declared specialized models require chat opt-in.
	return !specializedCapabilities.some((capability) => caps[capability] === true);
}
export const capableModels = (models: DirectoryModel[], capability?: string) =>
	models.filter(
		(model) =>
			!capability ||
			(capability === 'chat'
				? chatCapable(model)
				: specializedCapabilities.includes(capability)
					? model.capabilities?.[capability] === true && !model.direct
					: (model.info?.meta?.capabilities?.[capability] ?? model.capabilities?.[capability]) ===
						true)
	);

export function resolveModelReference(
	models: { id: string; aliases?: string[] }[],
	reference: string
): string | null {
	if (models.some((model) => model.id === reference)) return reference;
	const matches = models.filter((model) => model.aliases?.includes(reference));
	return matches.length === 1 ? matches[0].id : null;
}

export const preserveModelReferences = (
	models: { id: string; aliases?: string[] }[],
	references: string[]
) => references.map((id) => resolveModelReference(models, id) ?? id);
