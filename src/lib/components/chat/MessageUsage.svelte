<script lang="ts">
	import { onDestroy } from 'svelte';
	import { Popover } from 'bits-ui';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import InfoCircle from '$lib/components/icons/InfoCircle.svelte';
	import { usageFor, dollars, addMoney } from '$lib/stores/oiUsage';
	import type { UsageOverview } from './MessageUsageOverview.svelte';

	export let chatId = '',
		messageId = '';
	export let nativeUsage: unknown = undefined;
	export let triggerClass = '';
	export let overview: UsageOverview | null = null;
	let nativeOpen = false;
	let disclosureInitialized = false;
	let open = false;
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
	const fields = [
		['输入', 'input_tokens'],
		['输出（含推理）', 'output_tokens'],
		['推理', 'reasoning_tokens'],
		['缓存读取', 'cache_read_tokens'],
		['缓存写入', 'cache_write_tokens'],
		['总 token', 'total_tokens']
	];
	const duration = (ms: number | undefined) =>
		ms == null ? '耗时未知' : ms < 100 ? '< 0.1 秒' : `${(ms / 1000).toFixed(1)} 秒`;
	const tokens = (items: any[], key: string) =>
		items.every((c) => c.usage?.[key] != null)
			? items.reduce((n, c) => n + c.usage[key], 0).toLocaleString()
			: '未知';
	const raw = (value: unknown) => JSON.stringify(value, null, 2);
	$: overview = calls.length
		? {
				messageId,
				status:
					calls.length === 1
						? `${statuses[calls[0].status] || calls[0].status} · ${duration(calls[0].usage?.duration_ms)}`
						: `${calls.length} 次调用`,
				metrics: fields.map(([label, key]) => ({ label, value: tokens(calls, key) })),
				actual,
				estimated,
				hasActual,
				hasEstimate,
				unknown,
				snapshot
			}
		: null;
	const disclosureClass =
		'focus-ring flex w-full cursor-pointer list-none items-center gap-1 rounded-sm py-1 text-left [&::-webkit-details-marker]:hidden';
</script>

{#if calls.length || nativeUsage || $store.error}
	<Popover.Root
		bind:open
		onOpenChange={(value) => {
			if (value) {
				if (!disclosureInitialized) {
					nativeOpen = calls.length === 0;
					disclosureInitialized = true;
				}
				void store.refresh();
			}
		}}
	>
		<Popover.Trigger
			id="info-{messageId}"
			aria-label="用量与费用"
			title="用量与费用"
			data-testid="message-info-trigger"
			class="focus-ring inline-flex size-7 shrink-0 items-center justify-center rounded-lg p-1.5 transition hover:bg-black/5 hover:text-gray-600 dark:hover:bg-white/5 dark:hover:text-gray-400 {triggerClass}"
			><InfoCircle className="size-4" /></Popover.Trigger
		>
		<Popover.Portal>
			<Popover.Content
				role="dialog"
				aria-label="用量与费用"
				data-testid="message-info-popover"
				side="top"
				align="start"
				sideOffset={6}
				collisionPadding={12}
				trapFocus={false}
				class="z-[10000] w-[26rem] max-w-[calc(100vw-1.5rem)] overflow-y-auto overscroll-contain whitespace-normal rounded-xl border border-gray-200/70 bg-white p-2.5 text-xs text-gray-600 shadow-lg outline-hidden dark:border-gray-700/60 dark:bg-gray-850 dark:text-gray-300"
				style="max-height: min(30rem, var(--bits-popover-content-available-height));"
			>
				<div class="flex items-center justify-between gap-3 pb-1.5">
					<span class="font-medium text-gray-800 dark:text-gray-100">调用明细与原始用量</span>
					{#if calls.length === 1}<span class="text-[0.6875rem] tabular-nums"
							>{statuses[calls[0].status] || calls[0].status} · {duration(
								calls[0].usage?.duration_ms
							)}</span
						>
					{:else if calls.length}<span class="text-[0.6875rem]">{calls.length} 次调用</span>{/if}
				</div>
				{#if $store.error}<p
						role="status"
						class="mb-1.5 text-[0.6875rem] text-amber-700 dark:text-amber-400"
					>
						账本暂时无法更新，已获取的数据保留。
					</p>{/if}
				{#if calls.length}
					<details class="group/calls mt-1.5 border-t border-gray-100 pt-1 dark:border-white/5">
						<summary class={disclosureClass}
							><span class="-rotate-90 transition-transform group-open/calls:rotate-0"
								><ChevronDown className="size-3" /></span
							>调用明细</summary
						>
						<div class="divide-y divide-gray-100 dark:divide-white/5">
							{#each calls as call, index (call.id)}<div class="space-y-1 py-1.5 text-[0.6875rem]">
									<div class="flex justify-between gap-2">
										<span
											>{call.usage?.archive_origin ? '导入调用' : '模型调用'}
											{index + 1} · {statuses[call.status] || call.status}</span
										><span class="tabular-nums">{duration(call.usage?.duration_ms)}</span>
									</div>
									{#if calls.length > 1}<div class="flex flex-wrap gap-x-3">
											<span>输入 {tokens([call], 'input_tokens')}</span><span
												>输出 {tokens([call], 'output_tokens')}</span
											><span>实际 {dollars(call.actual_usd)}</span><span
												>估算 {dollars(call.estimated_usd)}</span
											>
										</div>{/if}
									<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5">
										<dt>计费来源</dt>
										<dd>{call.source || call.pricing?.source || '未知'}</dd>
										<dt>调用 ID</dt>
										<dd class="break-all font-mono">{call.id}</dd>
										{#if call.usage?.finish}<dt>结束原因</dt>
											<dd>
												{call.usage.finish.reason || '未知'} · {call.usage.finish.protocol}
											</dd>{/if}
									</dl>
									{#if call.usage}<details class="group/raw-call">
											<summary class={disclosureClass}
												><span class="-rotate-90 transition-transform group-open/raw-call:rotate-0"
													><ChevronDown className="size-3" /></span
												>调用原始用量</summary
											>
											<pre
												class="whitespace-pre-wrap break-all rounded-md bg-gray-50 p-2 font-mono text-[0.6875rem] dark:bg-black/20">{raw(
													call.usage
												)}</pre>
										</details>{/if}
								</div>{/each}
						</div>
					</details>
				{:else}<p class="mb-1.5 text-[0.6875rem] text-gray-400">
						此消息暂无调用账本，费用未知。
					</p>{/if}
				{#if nativeUsage}<details
						class="group/raw-message border-t border-gray-100 pt-1 dark:border-white/5"
						bind:open={nativeOpen}
						data-testid="message-native-usage"
					>
						<summary class={disclosureClass}
							><span class="-rotate-90 transition-transform group-open/raw-message:rotate-0"
								><ChevronDown className="size-3" /></span
							>原始用量</summary
						>
						<pre
							class="whitespace-pre-wrap break-all rounded-md bg-gray-50 p-2 font-mono text-[0.6875rem] dark:bg-black/20">{raw(
								nativeUsage
							)}</pre>
					</details>{/if}
			</Popover.Content>
		</Popover.Portal>
	</Popover.Root>
{/if}
