<script lang="ts">
	import { onDestroy } from 'svelte';
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
	const tokens = (items: any[], key: string) =>
		items.every((c) => c.usage?.[key] != null)
			? items.reduce((n, c) => n + c.usage[key], 0).toLocaleString()
			: '未知';
</script>

{#if calls.length}
	<details class="mt-2 text-[0.6875rem] leading-relaxed text-gray-500 dark:text-gray-400">
		<summary class="cursor-pointer"
			>输入 {tokens(calls, 'input_tokens')} · 输出 {tokens(calls, 'output_tokens')} · 实际 {dollars(
				actual
			)} · 估算 {dollars(estimated)}{unknown ? ` · ${unknown} 次费用未知` : ''}</summary
		>
		<div
			class="mt-2 space-y-2 rounded-xl bg-gray-50 p-3 text-gray-600 dark:text-gray-300 dark:bg-white/[0.025]"
		>
			{#if snapshot}<p>
					截至这轮：实际 {dollars(snapshot.actual_usd)} · 估算 {dollars(snapshot.estimated_usd)} · 未知
					{snapshot.unknown_calls}
				</p>{/if}
			{#each calls as call (call.id)}
				<div class="rounded-lg bg-gray-50 p-2 dark:bg-gray-850">
					<p>
						{#if call.usage.archive_origin}<span class="me-1">导入记录 ·</span>{/if}
						{call.status} · {call.usage.duration_ms != null
							? `${(call.usage.duration_ms / 1000).toFixed(1)}s`
							: '耗时未知'} · 来源 {call.source || call.pricing?.source || '未知'}
					</p>
					<p>
						推理 {call.usage.reasoning_tokens ?? '未知'} · 缓存读取 {call.usage.cache_read_tokens ??
							'未知'} · 缓存写入 {call.usage.cache_write_tokens ?? '未知'}
					</p>
					<p class="break-all">调用 {call.id}</p>
					{#if call.usage.finish}<p>
							结束原因：{call.usage.finish.reason || '未知'} · {call.usage.finish.protocol}
						</p>{/if}
				</div>
			{/each}
		</div>
	</details>
{/if}
