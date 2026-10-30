<script context="module" lang="ts">
	export type { UsageOverview } from '$lib/utils/messageUsage';
</script>

<script lang="ts">
	import type { UsageOverview } from '$lib/utils/messageUsage';
	import { compactUsage, exactUsage, usageDuration } from '$lib/utils/messageUsage';
	import { dollars } from '$lib/stores/oiUsage';
	import UsageMetricIcon from './UsageMetricIcon.svelte';
	export let data: UsageOverview | null = null;
</script>

{#if data}
	<dl
		aria-label="本条回复用量"
		data-testid="message-usage-overview"
		class="usage-overview mb-1 mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.6875rem] leading-5 text-gray-500 dark:text-gray-400"
	>
		{#each data.metrics as metric (metric.key)}
			<div
				data-usage-field={metric.key}
				class="usage-metric inline-flex min-w-0 items-center gap-1 whitespace-nowrap"
				style={`grid-area: ${metric.key}`}
				title={`${metric.label}：${exactUsage(metric.value)} token`}
			>
				<dt class="inline-flex items-center gap-1">
					<UsageMetricIcon kind={metric.key} />
					<span>{metric.shortLabel}</span><span class="sr-only"
						>{metric.key === 'output_tokens' ? '（含推理）' : ''}</span
					>
				</dt>
				<dd class="tabular-nums text-gray-600 dark:text-gray-300">{compactUsage(metric.value)}</dd>
			</div>
		{/each}
		<div
			data-usage-field="cost"
			class="usage-cost inline-flex min-w-0 max-w-full flex-wrap items-center gap-x-1.5"
			title="本条回复费用；≈ 为估算金额，待确认不代表免费"
		>
			<dt class="inline-flex items-center gap-1 whitespace-nowrap">
				<UsageMetricIcon kind="cost" />费用
			</dt>
			<dd class="flex min-w-0 flex-wrap items-center gap-x-1.5 tabular-nums">
				{#if data.hasActual}<span class="break-all text-gray-700 dark:text-gray-200"
						>{dollars(data.actual)}</span
					>{/if}
				{#if data.hasEstimate}<span class="break-all" aria-label={`估算 ${dollars(data.estimated)}`}
						>≈{dollars(data.estimated)}</span
					>{/if}
				{#if data.unknown}<span class="whitespace-nowrap"
						>{data.unknown > 1 ? `${data.unknown} 次` : ''}待确认</span
					>{/if}
			</dd>
		</div>
		{#if data.snapshot}<div
				data-usage-field="cumulative-cost"
				class="usage-cumulative inline-flex min-w-0 max-w-full flex-wrap items-center gap-x-1.5"
				title="截至本轮的会话累计，实际与估算分开；包含已发生的重新生成费用"
			>
				<dt class="inline-flex items-center gap-1"><UsageMetricIcon kind="cumulative" />累计</dt>
				<dd class="flex min-w-0 flex-wrap gap-x-1.5 tabular-nums">
					<span class="break-all">{dollars(data.snapshot.actual_usd)}</span>
					{#if data.snapshot.estimated_usd && data.snapshot.estimated_usd !== '0'}<span
							class="break-all">≈{dollars(data.snapshot.estimated_usd)}</span
						>{/if}
					{#if data.snapshot.incomplete && !data.snapshot.unknown_baselines && !data.snapshot.unknown_calls}<span
							class="text-amber-700 dark:text-amber-400">累计尚不完整</span
						>{/if}{#if data.snapshot.unknown_baselines}<span
							class="whitespace-nowrap text-amber-700 dark:text-amber-400"
							>+ {data.snapshot.unknown_baselines} 批历史费用待确认</span
						>{/if}{#if data.snapshot.unknown_calls}<span class="whitespace-nowrap"
							>+ {data.snapshot.unknown_calls} 次待确认</span
						>{/if}
				</dd>
			</div>{/if}
		{#if data.durationMs != null}<div
				data-usage-field="duration"
				class="usage-timing inline-flex items-center gap-1 whitespace-nowrap"
				title={`本次调用耗时：${usageDuration(data.durationMs)}`}
			>
				<dt class="inline-flex">
					<UsageMetricIcon kind="duration" /><span class="sr-only">耗时</span>
				</dt>
				<dd class="tabular-nums">{usageDuration(data.durationMs, true)}</dd>
			</div>{/if}
		{#if data.callCount > 1}<div
				data-usage-field="calls"
				class="usage-timing inline-flex items-center gap-1 whitespace-nowrap"
			>
				<dt class="inline-flex items-center gap-1"><UsageMetricIcon kind="calls" />调用</dt>
				<dd class="tabular-nums">{data.callCount} 次</dd>
			</div>{/if}
		{#if data.abnormal}<div
				data-usage-field="status"
				class="usage-status inline-flex items-center gap-1 text-amber-700 dark:text-amber-400"
			>
				<dt class="inline-flex">
					<UsageMetricIcon kind="status" /><span class="sr-only">状态</span>
				</dt>
				<dd>{data.status}</dd>
			</div>{/if}
	</dl>
{/if}

<style>
	@media (max-width: 639px) {
		.usage-overview {
			display: grid;
			grid-template-columns: repeat(6, minmax(0, 1fr));
			grid-template-areas:
				'input_tokens input_tokens output_tokens output_tokens reasoning_tokens reasoning_tokens'
				'cache_read_tokens cache_read_tokens cache_write_tokens cache_write_tokens total_tokens total_tokens'
				'cost cost cost cumulative cumulative cumulative'
				'timing timing status status status status';
			column-gap: 0.875rem;
			row-gap: 0.125rem;
			align-items: baseline;
		}
		.usage-metric {
			justify-content: space-between;
		}
		.usage-cost {
			grid-area: cost;
		}
		.usage-timing {
			grid-area: timing;
			justify-self: start;
		}
		.usage-cumulative {
			grid-area: cumulative;
		}
		.usage-status {
			grid-area: status;
			justify-self: end;
			text-align: end;
		}
	}
	@media (max-width: 359px) {
		.usage-overview {
			grid-template-columns: repeat(2, minmax(0, 1fr));
			grid-template-areas:
				'input_tokens output_tokens'
				'cache_read_tokens cache_write_tokens'
				'reasoning_tokens total_tokens'
				'cost cumulative'
				'timing status';
		}
	}
</style>
