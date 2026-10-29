<script context="module" lang="ts">
	export type { UsageOverview } from '$lib/utils/messageUsage';
</script>

<script lang="ts">
	import type { UsageOverview } from '$lib/utils/messageUsage';
	import { compactUsage, exactUsage, usageDuration } from '$lib/utils/messageUsage';
	import { dollars } from '$lib/stores/oiUsage';
	import ArrowUpTray from '$lib/components/icons/ArrowUpTray.svelte';
	import ArrowDownTray from '$lib/components/icons/ArrowDownTray.svelte';
	import Database from '$lib/components/icons/Database.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import Hashtag from '$lib/components/icons/Hashtag.svelte';
	import CurrencyDollar from '$lib/components/icons/CurrencyDollar.svelte';
	import ClockRotateRight from '$lib/components/icons/ClockRotateRight.svelte';
	export let data: UsageOverview | null = null;
	$: cacheRead = data?.metrics.find((metric) => metric.key === 'cache_read_tokens')?.value ?? null;
	$: cacheWrite =
		data?.metrics.find((metric) => metric.key === 'cache_write_tokens')?.value ?? null;
	const icons = {
		input_tokens: ArrowUpTray,
		output_tokens: ArrowDownTray,
		cache_read_tokens: Database,
		cache_write_tokens: Database,
		reasoning_tokens: Sparkles,
		total_tokens: Hashtag
	};
	const tones = {
		input_tokens: 'text-sky-600 dark:text-sky-400',
		output_tokens: 'text-emerald-600 dark:text-emerald-400',
		cache_read_tokens: 'text-violet-500 dark:text-violet-400',
		cache_write_tokens: 'text-violet-500 dark:text-violet-400',
		reasoning_tokens: 'text-violet-500 dark:text-violet-400',
		total_tokens: 'text-gray-400 dark:text-gray-500'
	};
</script>

{#if data}
	<dl
		aria-label="本条回复用量"
		data-testid="message-usage-overview"
		class="mb-1 mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.6875rem] leading-5 text-gray-500 dark:text-gray-400"
	>
		{#each data.metrics as metric (metric.key)}
			{#if metric.key === 'cache_read_tokens'}
				<div class="inline-flex items-center gap-1 whitespace-nowrap">
					<dt class="inline-flex items-center gap-1">
						<Database className="size-3 shrink-0 text-violet-500 dark:text-violet-400" />缓存
					</dt>
					<dd class="inline-flex items-center gap-1 tabular-nums">
						<span
							data-usage-field="cache_read_tokens"
							title={`缓存读取：${exactUsage(cacheRead)} token`}
							>读 <span class="text-gray-600 dark:text-gray-300">{compactUsage(cacheRead)}</span
							></span
						><span aria-hidden="true" class="text-gray-300 dark:text-gray-600">/</span><span
							data-usage-field="cache_write_tokens"
							title={`缓存写入：${exactUsage(cacheWrite)} token`}
							>写 <span class="text-gray-600 dark:text-gray-300">{compactUsage(cacheWrite)}</span
							></span
						>
					</dd>
				</div>
			{:else if metric.key !== 'cache_write_tokens'}
				<div
					data-usage-field={metric.key}
					class="inline-flex items-center gap-1 whitespace-nowrap"
					title={`${metric.label}：${exactUsage(metric.value)} token`}
				>
					<dt class="inline-flex items-center gap-1">
						<svelte:component
							this={icons[metric.key]}
							className={`size-3 shrink-0 ${tones[metric.key]}`}
						/><span>{metric.shortLabel}</span><span class="sr-only"
							>{metric.key === 'output_tokens' ? '（含推理）' : ''}</span
						>
					</dt>
					<dd class="tabular-nums text-gray-600 dark:text-gray-300">
						{compactUsage(metric.value)}
					</dd>
				</div>
			{/if}
		{/each}
		<div
			data-usage-field="cost"
			class="inline-flex max-w-full flex-wrap items-center gap-x-1.5"
			title="本条回复费用；≈ 为估算金额，待确认不代表免费"
		>
			<dt class="inline-flex items-center gap-1 whitespace-nowrap">
				<CurrencyDollar className="size-3 shrink-0 text-amber-600 dark:text-amber-400" />费用
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
		{#if data.durationMs != null}<div
				data-usage-field="duration"
				class="inline-flex items-center gap-1 whitespace-nowrap"
				title={`本次调用耗时：${usageDuration(data.durationMs)}`}
			>
				<dt class="inline-flex">
					<ClockRotateRight className="size-3 text-gray-400 dark:text-gray-500" /><span
						class="sr-only">耗时</span
					>
				</dt>
				<dd class="tabular-nums">{usageDuration(data.durationMs, true)}</dd>
			</div>{/if}
		{#if data.callCount > 1}<div class="inline-flex items-center gap-1 whitespace-nowrap">
				<dt>调用</dt>
				<dd class="tabular-nums">{data.callCount} 次</dd>
			</div>{/if}
		{#if data.abnormal}<div class="text-amber-700 dark:text-amber-400">
				<dt class="sr-only">状态</dt>
				<dd>{data.status}</dd>
			</div>{/if}
		{#if data.snapshot}<div
				data-usage-field="cumulative-cost"
				class="inline-flex max-w-full flex-wrap items-center gap-x-1.5"
				title="截至本轮的会话累计，实际与估算分开；包含已发生的重新生成费用"
			>
				<dt>累计</dt>
				<dd class="flex min-w-0 flex-wrap gap-x-1.5 tabular-nums">
					<span class="break-all">{dollars(data.snapshot.actual_usd)}</span
					>{#if data.snapshot.estimated_usd && data.snapshot.estimated_usd !== '0'}<span
							class="break-all">≈{dollars(data.snapshot.estimated_usd)}</span
						>{/if}{#if data.snapshot.unknown_calls}<span class="whitespace-nowrap"
							>+ {data.snapshot.unknown_calls} 次待确认</span
						>{/if}
				</dd>
			</div>{/if}
	</dl>
{/if}
