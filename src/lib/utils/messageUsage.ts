export const usageFields = [
	['input_tokens', '输入', '输入'],
	['output_tokens', '输出（含推理）', '输出'],
	['cache_read_tokens', '缓存读取', '缓存读'],
	['cache_write_tokens', '缓存写入', '缓存写'],
	['reasoning_tokens', '推理', '推理'],
	['total_tokens', '总 token', '总计']
] as const;

export type UsageMetric = {
	key: (typeof usageFields)[number][0];
	label: string;
	shortLabel: string;
	value: number | null;
};

export type UsageOverview = {
	messageId: string;
	status: string;
	abnormal: boolean;
	callCount: number;
	durationMs: number | null;
	metrics: UsageMetric[];
	actual: string;
	estimated: string;
	hasActual: boolean;
	hasEstimate: boolean;
	unknown: number;
	snapshot: any;
};

export function uniqueUsageCalls<T extends { id: string }>(calls: T[]): T[] {
	return [...new Map(calls.map((call) => [call.id, call])).values()];
}

/** A missing observation never becomes a measured zero or a complete subtotal. */
export function usageMetrics(calls: { usage?: Record<string, any> | null }[]): UsageMetric[] {
	return usageFields.map(([key, label, shortLabel]) => ({
		key,
		label,
		shortLabel,
		value:
			calls.length &&
			calls.every(
				(call) => typeof call.usage?.[key] === 'number' && Number.isFinite(call.usage[key])
			)
				? calls.reduce((sum, call) => sum + call.usage![key], 0)
				: null
	}));
}

export const exactUsage = (value: number | null) =>
	value == null ? '未知' : value.toLocaleString();
export const compactUsage = (value: number | null) =>
	value == null
		? '—'
		: new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
export const usageDuration = (ms: number | null | undefined, short = false) =>
	ms == null ? '耗时未知' : `${ms < 100 ? '<0.1' : (ms / 1000).toFixed(1)}${short ? 's' : ' 秒'}`;

/** Signatures and token counts are not readable reasoning text. Legacy markup is unspecified. */
export function hasReadableThoughts(output: unknown): boolean | null {
	if (!Array.isArray(output)) return null;
	return output.some(
		(item) =>
			item?.type === 'reasoning' &&
			[item.summary, item.content].some(
				(parts) =>
					Array.isArray(parts) &&
					parts.some((part) => typeof part?.text === 'string' && part.text.trim().length > 0)
			)
	);
}
