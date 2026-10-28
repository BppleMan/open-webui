<script lang="ts">
	import { onDestroy } from 'svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import { usageFor, dollars, addMoney } from '$lib/stores/oiUsage';
	export let chatId = '',
		messageId = '';
	let store = usageFor(chatId),
		disconnect: (() => void) | undefined,
		active = '';
	$: if (chatId !== active) {
		disconnect?.();
		active = chatId;
		store = usageFor(chatId);
		if (chatId) disconnect = store.connect();
	}
	onDestroy(() => disconnect?.());
	$: generations = new Set(
		$store.generations.filter((g) => g.message_id === messageId).map((g) => g.id)
	);
	$: calls = $store.calls.filter((c) => generations.has(c.generation_id));
	$: snapshot = $store.snapshots.filter((s) => s.message_id === messageId).at(-1);
	$: actual = addMoney(calls.map((c) => c.actual_usd));
	$: estimated = addMoney(calls.map((c) => c.estimated_usd));
	$: unknown = calls.filter((c) => c.actual_usd == null && c.estimated_usd == null).length;
	$: hasActual = calls.some((c) => c.actual_usd != null);
	$: hasEstimate = calls.some((c) => c.estimated_usd != null);
	const statuses: Record<string, string> = {
		completed: '已完成',
		failed: '请求失败',
		cancelled: '已停止',
		interrupted: '连接中断',
		running: '生成中',
		pending: '等待中'
	};
	const duration = (ms: number | undefined) =>
		ms == null ? '耗时未知' : ms < 100 ? '< 0.1 秒' : `${(ms / 1000).toFixed(1)} 秒`;
	const tokens = (items: any[], key: string) =>
		items.every((c) => c.usage?.[key] != null)
			? items.reduce((n, c) => n + c.usage[key], 0).toLocaleString()
			: '未知';
</script>

{#if calls.length}
	<details
		class="group/usage mt-2 max-w-[36rem] text-xs text-gray-500 dark:text-gray-400"
		data-testid="message-usage"
	>
		<summary
			class="focus-ring flex w-fit cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1 rounded-md py-1 text-[0.6875rem] [&::-webkit-details-marker]:hidden"
		>
			<span class="transition-transform group-open/usage:rotate-180"
				><ChevronDown className="size-3" /></span
			>
			<span>输入 {tokens(calls, 'input_tokens')}</span><span
				class="text-gray-300 dark:text-gray-600">/</span
			><span>输出 {tokens(calls, 'output_tokens')}</span>
			{#if hasActual}<span>实际 {dollars(actual)}</span>{/if}
			{#if hasEstimate}<span>估算 {dollars(estimated)}</span>{/if}
			{#if unknown}<span class="text-amber-700 dark:text-amber-400">{unknown} 次费用待确认</span
				>{/if}
		</summary>
		<div
			class="mt-1 overflow-hidden rounded-xl border border-gray-100 bg-gray-50/60 dark:border-white/[0.06] dark:bg-white/[0.025]"
		>
			{#if snapshot}
				<div class="border-b border-gray-100 px-4 py-3 dark:border-white/[0.06]">
					<div class="mb-2 text-[0.6875rem] font-medium text-gray-500">截至本轮的会话累计</div>
					<div class="flex flex-wrap gap-x-6 gap-y-2">
						<div>
							<span class="mr-2 text-gray-500">实际</span><span
								class="font-medium tabular-nums text-gray-800 dark:text-gray-200"
								>{dollars(snapshot.actual_usd)}</span
							>
						</div>
						<div>
							<span class="mr-2 text-gray-500">估算</span><span
								class="font-medium tabular-nums text-gray-800 dark:text-gray-200"
								>{dollars(snapshot.estimated_usd)}</span
							>
						</div>
						{#if snapshot.unknown_calls}<span class="text-amber-700 dark:text-amber-400"
								>{snapshot.unknown_calls} 次费用待确认</span
							>{/if}
					</div>
				</div>
			{/if}
			<div class="divide-y divide-gray-100 dark:divide-white/[0.06]">
				{#each calls as call, index (call.id)}
					<div class="space-y-3 px-4 py-3">
						<div class="flex items-center justify-between gap-3">
							<div class="flex items-center gap-2">
								<span class="font-medium text-gray-700 dark:text-gray-200"
									>{call.usage?.archive_origin ? '导入调用' : '模型调用'}{calls.length > 1
										? ` ${index + 1}`
										: ''}</span
								>
								<span
									class="rounded-md px-1.5 py-0.5 text-[0.6875rem] {call.status === 'failed'
										? 'bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-300'
										: 'bg-gray-100 text-gray-600 dark:bg-white/5 dark:text-gray-400'}"
									>{statuses[call.status] || call.status}</span
								>
							</div>
							<span class="text-[0.6875rem] tabular-nums">{duration(call.usage?.duration_ms)}</span>
						</div>
						<dl class="grid grid-cols-2 gap-x-6 gap-y-2 text-[0.6875rem] sm:grid-cols-3">
							{#each [['输入', 'input_tokens'], ['输出', 'output_tokens'], ['推理', 'reasoning_tokens'], ['缓存读取', 'cache_read_tokens'], ['缓存写入', 'cache_write_tokens']] as [label, key]}
								<div class="flex justify-between gap-2">
									<dt>{label}</dt>
									<dd class="tabular-nums text-gray-700 dark:text-gray-300">
										{call.usage?.[key]?.toLocaleString() ?? '未知'}
									</dd>
								</div>
							{/each}
						</dl>
						<div class="flex flex-wrap gap-x-4 gap-y-1 text-[0.6875rem]">
							{#if call.actual_usd != null}<span
									>实际 <strong class="font-medium tabular-nums text-gray-800 dark:text-gray-200"
										>{dollars(call.actual_usd)}</strong
									></span
								>{/if}
							{#if call.estimated_usd != null}<span
									>估算 <strong class="font-medium tabular-nums text-gray-800 dark:text-gray-200"
										>{dollars(call.estimated_usd)}</strong
									></span
								>{/if}
							{#if call.actual_usd == null && call.estimated_usd == null}<span
									class="text-amber-700 dark:text-amber-400"
									>提供方尚未返回费用，未按免费计算。</span
								>{/if}
						</div>
						<details class="group/call text-[0.6875rem]">
							<summary
								class="focus-ring flex w-fit cursor-pointer list-none items-center gap-1 rounded-sm [&::-webkit-details-marker]:hidden"
								><span class="transition-transform group-open/call:rotate-180"
									><ChevronDown className="size-3" /></span
								>调用信息</summary
							>
							<dl class="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-gray-500">
								<dt>计费来源</dt>
								<dd>{call.source || call.pricing?.source || '未知'}</dd>
								<dt>调用 ID</dt>
								<dd class="break-all font-mono">{call.id}</dd>
								{#if call.usage?.finish}<dt>结束原因</dt>
									<dd>{call.usage.finish.reason || '未知'} · {call.usage.finish.protocol}</dd>{/if}
							</dl>
						</details>
					</div>
				{/each}
			</div>
		</div>
	</details>
{/if}
