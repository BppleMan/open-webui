<script lang="ts">
	import { onDestroy } from 'svelte';
	import { Popover, Tabs } from 'bits-ui';
	import InfoCircle from '$lib/components/icons/InfoCircle.svelte';
	import UsageMetricIcon from './UsageMetricIcon.svelte';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';
	import { usageFor, dollars, addMoney } from '$lib/stores/oiUsage';
	import {
		usageMetrics,
		uniqueUsageCalls,
		exactUsage,
		usageDuration,
		type UsageOverview
	} from '$lib/utils/messageUsage';

	export let chatId = '',
		messageId = '';
	export let nativeUsage: unknown = undefined;
	export let readableThoughts: boolean | null = null;
	export let triggerClass = '';
	export let overview: UsageOverview | null = null;
	let open = false,
		tab = 'overview',
		rawSource = 'native',
		initialized = '';
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
	$: calls = uniqueUsageCalls($store.calls.filter((c) => generations.has(c.generation_id)));
	$: snapshot = $store.snapshots.filter((s) => s.message_id === messageId).at(-1);
	$: actual = addMoney(calls.map((c) => c.actual_usd));
	$: estimated = addMoney(calls.map((c) => c.estimated_usd));
	$: unknown = calls.filter((c) => c.actual_usd == null && c.estimated_usd == null).length;
	$: hasActual = calls.some((c) => c.actual_usd != null);
	$: hasEstimate = calls.some((c) => c.estimated_usd != null);
	$: metrics = usageMetrics(calls);
	const statuses: Record<string, string> = {
		completed: '已完成',
		failed: '请求失败',
		cancelled: '已停止',
		interrupted: '连接中断',
		running: '生成中',
		pending: '等待中',
		incomplete: '回复不完整',
		blocked: '内容被拦截',
		paused: '等待继续'
	};
	$: abnormal = calls.some((c) =>
		['failed', 'cancelled', 'interrupted', 'incomplete', 'blocked', 'paused'].includes(c.status)
	);
	$: status =
		calls.length === 1
			? statuses[calls[0].status] || calls[0].status
			: abnormal
				? '含异常结束的调用'
				: `${calls.length} 次调用`;
	$: overview = calls.length
		? {
				messageId,
				status,
				abnormal,
				callCount: calls.length,
				durationMs: calls.length === 1 ? (calls[0].usage?.duration_ms ?? null) : null,
				metrics,
				actual,
				estimated,
				hasActual,
				hasEstimate,
				unknown,
				snapshot
			}
		: null;
	$: rawOptions = [
		...(nativeUsage != null ? [{ id: 'native', label: '回复原始 usage', value: nativeUsage }] : []),
		...calls.map((call, index) => ({
			id: `call:${call.id}`,
			label: `调用 ${index + 1} · 完整记录`,
			value: call
		}))
	];
	// Selected source is keyed by immutable call ID and never reset by polling.
	$: if (rawOptions.length && !rawOptions.some((source) => source.id === rawSource))
		rawSource = rawOptions[0].id;
	$: selectedRaw = rawOptions.find((source) => source.id === rawSource);
	const tabClass =
		'focus-ring rounded-md px-2.5 py-1.5 text-xs text-gray-500 data-[state=active]:bg-gray-100 data-[state=active]:font-medium data-[state=active]:text-gray-900 dark:text-gray-400 dark:data-[state=active]:bg-white/10 dark:data-[state=active]:text-white';
</script>

{#snippet tokens(items: typeof metrics)}
	<dl class="grid grid-cols-2 gap-x-4 gap-y-1">
		{#each items as metric (metric.key)}<div class="flex min-w-0 justify-between gap-2">
				<dt class="inline-flex min-w-0 items-center gap-1.5 text-gray-500 dark:text-gray-400">
					<UsageMetricIcon kind={metric.key} />{metric.label}
				</dt>
				<dd class="tabular-nums text-gray-800 dark:text-gray-200">{exactUsage(metric.value)}</dd>
			</div>{/each}
	</dl>
{/snippet}

{#if calls.length || nativeUsage || $store.error}
	<Popover.Root
		bind:open
		onOpenChange={(value) => {
			if (value) {
				const key = `${chatId}:${messageId}`;
				if (initialized !== key) {
					tab = calls.length ? 'overview' : 'raw';
					initialized = key;
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
				<div class="flex items-center justify-between gap-3 pb-1">
					<span
						class="inline-flex items-center gap-1.5 font-medium text-gray-800 dark:text-gray-100"
						><UsageMetricIcon kind="cost" className="size-3.5" />用量与费用</span
					>{#if calls.length}<span class="text-[0.6875rem]">{status}</span>{/if}
				</div>
				{#if $store.error}<p
						role="status"
						class="mb-1.5 text-[0.6875rem] text-amber-700 dark:text-amber-400"
					>
						账本暂时无法更新，已获取的数据保留。
					</p>{/if}
				<Tabs.Root bind:value={tab}>
					<Tabs.List
						aria-label="用量详情"
						class="mb-2 flex gap-1 border-b border-gray-100 py-1 dark:border-white/5"
						><Tabs.Trigger value="overview" class={tabClass}>概览</Tabs.Trigger><Tabs.Trigger
							value="calls"
							class={tabClass}>调用{calls.length ? ` ${calls.length}` : ''}</Tabs.Trigger
						><Tabs.Trigger value="raw" class={tabClass}>原始数据</Tabs.Trigger></Tabs.List
					>
					<Tabs.Content value="overview" class="outline-hidden" data-testid="message-info-overview">
						{#if calls.length}
							{@render tokens(metrics)}
							<div
								class="mt-2 space-y-1 border-t border-gray-100 pt-2 text-[0.6875rem] dark:border-white/5"
							>
								<div class="flex flex-wrap gap-x-3 gap-y-0.5">
									<span class="inline-flex items-center gap-1.5 text-gray-500 dark:text-gray-400"
										><UsageMetricIcon kind="cost" />本条回复</span
									><span
										>实际 <span class="tabular-nums">{hasActual ? dollars(actual) : '未知'}</span
										></span
									><span
										>估算 <span class="tabular-nums">{hasEstimate ? dollars(estimated) : '—'}</span
										></span
									>{#if unknown}<span class="text-amber-700 dark:text-amber-400"
											>{unknown} 次费用待确认</span
										>{/if}
								</div>
								{#if snapshot}<div class="flex flex-wrap gap-x-3 gap-y-0.5">
										<span class="inline-flex items-center gap-1.5 text-gray-500 dark:text-gray-400"
											><UsageMetricIcon kind="cumulative" />截至本轮会话</span
										><span
											>实际 <span class="tabular-nums">{dollars(snapshot.actual_usd)}</span></span
										><span
											>估算 <span class="tabular-nums">{dollars(snapshot.estimated_usd)}</span
											></span
										>{#if snapshot.incomplete && !snapshot.unknown_baselines && !snapshot.unknown_calls}<span
												class="text-amber-700 dark:text-amber-400">累计尚不完整</span
											>{/if}{#if snapshot.unknown_baselines}<span
												class="whitespace-nowrap text-amber-700 dark:text-amber-400"
												>+ {snapshot.unknown_baselines} 批历史费用待确认</span
											>{/if}{#if snapshot.unknown_calls}<span
												class="text-amber-700 dark:text-amber-400"
												>{snapshot.unknown_calls} 次待确认</span
											>{/if}
									</div>{/if}
								{#if calls.length === 1}<div class="flex flex-wrap gap-x-3 gap-y-0.5">
										<span class="inline-flex items-center gap-1.5 text-gray-500 dark:text-gray-400"
											><UsageMetricIcon kind="duration" />耗时</span
										><span class="tabular-nums">{usageDuration(calls[0].usage?.duration_ms)}</span
										>{#if calls[0].usage?.first_token_ms != null}<span
												class="text-gray-500 dark:text-gray-400">首 token</span
											><span class="tabular-nums"
												>{usageDuration(calls[0].usage.first_token_ms)}</span
											>{/if}
									</div>{/if}
							</div>
							{#if readableThoughts === false && metrics.some((metric) => metric.key === 'reasoning_tokens' && (metric.value ?? 0) > 0)}<p
									class="mt-2 text-[0.6875rem] text-gray-500 dark:text-gray-400"
								>
									有推理用量；当前回复没有可展示的思考摘要。
								</p>{/if}
						{:else}<p class="py-1 text-gray-500">
								此消息暂无调用账本，费用未知。原生字段可在“原始数据”查看。
							</p>{/if}
					</Tabs.Content>
					<Tabs.Content value="calls" class="outline-hidden" data-testid="message-info-calls">
						<div class="divide-y divide-gray-100 dark:divide-white/5">
							{#each calls as call, index (call.id)}<section
									class="py-2 first:pt-0 last:pb-0"
									aria-label={`模型调用 ${index + 1}`}
								>
									<div class="mb-1.5 flex justify-between gap-2 text-[0.6875rem]">
										<span class="font-medium"
											>{call.usage?.archive_origin ? '导入调用' : '模型调用'}
											{index + 1} · {statuses[call.status] || call.status}</span
										><span class="tabular-nums">{usageDuration(call.usage?.duration_ms)}</span>
									</div>
									{@render tokens(usageMetrics([call]))}
									<dl
										class="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-1 text-[0.6875rem]"
									>
										<dt class="inline-flex items-center gap-1.5 text-gray-500">
											<UsageMetricIcon kind="cost" />实际 / 估算
										</dt>
										<dd class="break-all tabular-nums">
											{dollars(call.actual_usd)} / {dollars(call.estimated_usd)}
										</dd>
										<dt class="inline-flex items-center gap-1.5 text-gray-500">
											<UsageMetricIcon kind="source" />计费来源
										</dt>
										<dd class="break-all">{call.source || call.pricing?.source || '未知'}</dd>
										<dt class="inline-flex items-center gap-1.5 text-gray-500">
											<UsageMetricIcon kind="calls" />调用 ID
										</dt>
										<dd class="break-all font-mono">{call.id}</dd>
										{#if call.usage?.finish}<dt
												class="inline-flex items-center gap-1.5 text-gray-500"
											>
												<UsageMetricIcon kind="status" />结束原因
											</dt>
											<dd>
												{call.usage.finish.reason || '未知'} · {call.usage.finish.protocol}
											</dd>{/if}
									</dl>
								</section>{:else}<p class="py-1 text-gray-500">此消息暂无调用账本。</p>{/each}
						</div>
					</Tabs.Content>
					<Tabs.Content value="raw" class="outline-hidden" data-testid="message-info-raw">
						{#if rawOptions.length}
							<SettingsSelect bind:value={rawSource} ariaLabel="原始数据来源" className="w-full"
								><option disabled value="">选择数据来源</option
								>{#each rawOptions as source (source.id)}<option value={source.id}
										>{source.label}</option
									>{/each}</SettingsSelect
							>
							<pre
								data-testid="message-native-usage"
								class="mt-2 whitespace-pre-wrap break-all rounded-md bg-gray-50 p-2 font-mono text-[0.6875rem] dark:bg-black/20">{JSON.stringify(
									selectedRaw?.value,
									null,
									2
								)}</pre>
						{:else}<p class="py-1 text-gray-500">暂无原始用量。</p>{/if}
					</Tabs.Content>
				</Tabs.Root>
			</Popover.Content>
		</Popover.Portal>
	</Popover.Root>
{/if}
