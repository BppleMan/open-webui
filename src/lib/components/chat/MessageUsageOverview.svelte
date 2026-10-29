<script context="module" lang="ts">
	export type UsageOverview = {
		messageId: string;
		status: string;
		metrics: { label: string; value: string }[];
		actual: string;
		estimated: string;
		hasActual: boolean;
		hasEstimate: boolean;
		unknown: number;
		snapshot: any;
	};
</script>

<script lang="ts">
	import { dollars } from '$lib/stores/oiUsage';
	export let data: UsageOverview | null = null;
</script>

{#if data}
	<section
		aria-label="本条回复用量"
		data-testid="message-usage-overview"
		class="mb-2 mt-1 w-full max-w-[36rem] rounded-lg border border-gray-100 bg-gray-50/60 px-3 py-2 text-[0.6875rem] text-gray-500 dark:border-white/[0.06] dark:bg-white/[0.025] dark:text-gray-400"
	>
		<div class="mb-1 flex items-center justify-between gap-3">
			<span class="font-medium text-gray-700 dark:text-gray-200">用量与费用</span>
			<span class="tabular-nums">{data.status}</span>
		</div>
		<dl class="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
			{#each data.metrics as metric}<div class="flex min-w-0 justify-between gap-2">
					<dt>{metric.label}</dt>
					<dd class="tabular-nums text-gray-800 dark:text-gray-200">{metric.value}</dd>
				</div>{/each}
		</dl>
		<div class="mt-1.5 space-y-1 border-t border-gray-100 pt-1.5 dark:border-white/5">
			<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
				<span>本条回复</span>
				{#if data.hasActual}<span
						>实际 <strong class="font-medium tabular-nums text-gray-800 dark:text-gray-200"
							>{dollars(data.actual)}</strong
						></span
					>{/if}
				{#if data.hasEstimate}<span
						>估算 <strong class="font-medium tabular-nums">{dollars(data.estimated)}</strong></span
					>{/if}
				{#if data.unknown}<span class="text-amber-700 dark:text-amber-400"
						>{data.unknown} 次费用待确认</span
					>{/if}
			</div>
			{#if data.snapshot}<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
					<span>截至本轮会话</span><span
						>实际 <span class="tabular-nums">{dollars(data.snapshot.actual_usd)}</span></span
					><span>估算 <span class="tabular-nums">{dollars(data.snapshot.estimated_usd)}</span></span
					>
					{#if data.snapshot.unknown_calls}<span class="text-amber-700 dark:text-amber-400"
							>{data.snapshot.unknown_calls} 次待确认</span
						>{/if}
				</div>{/if}
		</div>
	</section>
{/if}
