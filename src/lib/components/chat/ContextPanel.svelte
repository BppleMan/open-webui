<script lang="ts">
	import { onDestroy } from 'svelte';
	import { usageFor } from '$lib/stores/oiUsage';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UsageSummary from './UsageSummary.svelte';
	export let chatId = '',
		currentId: string | null = null,
		history: any = { messages: {} },
		model: any = null;
	export let compact: (() => void) | undefined = undefined;
	export let compactionEnabled = false;
	let store = usageFor(chatId),
		disconnect: (() => void) | undefined,
		active = '',
		show = false;
	$: if (chatId !== active) {
		disconnect?.();
		active = chatId;
		show = false;
		store = usageFor(chatId);
		if (chatId) disconnect = store.connect();
	}
	onDestroy(() => disconnect?.());
	const branchIds = (history: any, currentId: string | null) => {
		const ids = new Set<string>();
		let id = currentId;
		while (id && !ids.has(id)) {
			ids.add(id);
			id = history.messages?.[id]?.parentId;
		}
		return ids;
	};
	$: ids = branchIds(history, currentId);
	$: generationIds = new Set(
		$store.generations.filter((g) => ids.has(g.message_id)).map((g) => g.id)
	);
	$: calls = $store.calls.filter(
		(c) => generationIds.has(c.generation_id) && (!model || c.model_id === model.id)
	);
	$: contextCalls = calls.filter((c) =>
		['assistant', 'tool_followup'].includes(c.usage?.context?.purpose)
	);
	$: latest = contextCalls.at(-1) || calls[0];
	$: context = latest?.usage?.context;
	$: limit = model?.capabilities?.input_limit ?? context?.input_limit;
	$: count = latest?.usage?.input_tokens ?? context?.estimated_tokens;
	$: percent =
		limit > 0 && count != null ? Math.max(0, Math.min(100, (count / limit) * 100)) : null;
	$: remaining = limit > 0 && count != null ? Math.max(0, limit - count) : null;
	$: source =
		latest?.usage?.input_tokens != null
			? '最近一次实际输入'
			: count != null
				? '本地估算'
				: '尚无用量数据';
	$: label =
		count != null
			? `上下文窗口：已用 ${format(count)}，容量 ${format(limit)}${percent != null ? `，${Math.round(percent)}%` : ''}。${source}。点击查看详情`
			: '上下文窗口：尚无用量数据。点击查看详情';
	const labels: Record<string, string> = {
		system: '系统指令',
		messages: '消息',
		tool_results: '工具结果',
		tool_definitions: '工具定义'
	};
	const format = (value: number | null | undefined) =>
		value == null
			? '未知'
			: new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(
					value
				);
	const circumference = 2 * Math.PI * 8;
</script>

<Dropdown
	bind:show
	side="top"
	align="end"
	sideOffset={10}
	visualViewportAware={true}
	maxHeight="min(36rem, calc(100dvh - 2rem))"
	onOpenChange={(open) => {
		if (open) void store.refresh();
	}}
>
	<Tooltip content={show ? '' : label} placement="top" allowHTML={false}>
		<button
			type="button"
			aria-label={label}
			aria-expanded={show}
			aria-haspopup="dialog"
			data-testid="context-window-trigger"
			class="focus-ring flex size-8 shrink-0 items-center justify-center rounded-full text-gray-500 transition-colors hover:bg-black/5 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-gray-200"
		>
			<svg width="20" height="20" viewBox="0 0 20 20" class="-rotate-90" aria-hidden="true">
				<circle
					cx="10"
					cy="10"
					r="8"
					fill="none"
					stroke-width="2.4"
					class="stroke-gray-200 dark:stroke-gray-700"
					stroke-dasharray={percent == null ? '2 3' : undefined}
				/>
				{#if percent != null}<circle
						cx="10"
						cy="10"
						r="8"
						fill="none"
						stroke-width="2.4"
						stroke-linecap="round"
						stroke-dasharray={circumference}
						stroke-dashoffset={circumference * (1 - percent / 100)}
						class={percent >= 95
							? 'stroke-red-500'
							: percent >= 80
								? 'stroke-amber-500'
								: 'stroke-current'}
					/>{/if}
			</svg>
		</button>
	</Tooltip>
	<div
		slot="content"
		role="dialog"
		aria-label="上下文与会话用量"
		tabindex="-1"
		data-testid="context-window-popover"
		class="w-[23rem] max-w-[calc(100vw-1.5rem)] overflow-hidden rounded-2xl border border-gray-200/70 bg-white p-4 text-sm text-gray-700 shadow-xl dark:border-white/10 dark:bg-gray-850 dark:text-gray-200"
	>
		<div class="flex items-center justify-between gap-3">
			<h3 class="font-semibold">上下文窗口</h3>
			<span class="whitespace-nowrap font-mono text-xs tabular-nums"
				>{format(count)} / {format(limit)}{percent != null
					? ` (${Math.round(percent)}%)`
					: ''}</span
			>
		</div>
		<p class="mt-1 truncate text-xs text-gray-400 dark:text-gray-500">
			{model?.name ?? '当前模型'} · {source}
		</p>
		<div class="my-4 h-1.5 overflow-hidden rounded-full bg-gray-100 dark:bg-white/10">
			<div class="h-full rounded-full bg-blue-500" style={`width:${percent ?? 0}%`}></div>
		</div>
		<div class="space-y-2.5 text-[0.8125rem]">
			<div class="flex items-center gap-2">
				<span class="size-2 rounded-full bg-blue-500"></span><span class="flex-1">已用</span><span
					class="font-mono tabular-nums">{format(count)}</span
				>
			</div>
			<div class="flex items-center gap-2">
				<span class="size-2 rounded-full bg-gray-200 dark:bg-gray-700"></span><span class="flex-1"
					>剩余输入空间</span
				><span class="font-mono tabular-nums">{format(remaining)}</span>
			</div>
		</div>
		{#if percent != null && percent >= 80}<p
				class="mt-3 text-xs text-amber-600 dark:text-amber-400"
			>
				接近当前模型输入上限；不会自动压缩或裁剪历史。
			</p>{/if}
		{#if context}
			<details class="mt-4 text-xs">
				<summary class="cursor-pointer text-gray-500 dark:text-gray-400"
					>占用明细 <span class="text-gray-400 dark:text-gray-500">· 估算</span></summary
				>
				<div class="mt-3 space-y-2">
					{#each Object.entries(context.categories || {}) as [key, value]}<div
							class="flex justify-between gap-4"
						>
							<span>{labels[key] || key}</span><span class="font-mono"
								>≈{format(Number(value))}</span
							>
						</div>{/each}
				</div>
				<p class="mt-3 leading-relaxed text-gray-400 dark:text-gray-500">
					分类使用本地估算，与提供方实际计数可能不同。媒体、MCP、Skills、子代理分类中未能识别的部分保持未知。
				</p>
				{#if context.tools?.length}<details class="mt-3">
						<summary class="cursor-pointer">逐工具占用</summary>{#each context.tools as tool}<div
								class="mt-2 flex justify-between gap-4"
							>
								<span class="truncate">{tool.name}</span><span
									>≈{format(tool.estimated_tokens)}</span
								>
							</div>{/each}
					</details>{/if}
			</details>
		{/if}
		<div class="mt-4 space-y-2.5 border-t border-gray-100 pt-4 text-xs dark:border-white/5">
			<div class="flex justify-between gap-4">
				<span class="text-gray-500 dark:text-gray-400">缓存读取 / 写入</span><span class="font-mono"
					>{format(latest?.usage.cache_read_tokens)} / {format(
						latest?.usage.cache_write_tokens
					)}</span
				>
			</div>
			<div class="flex justify-between gap-4">
				<span class="text-gray-500 dark:text-gray-400">预留输出 / 输出上限</span><span
					class="font-mono"
					>{format(context?.reserved_output)} / {format(
						model?.capabilities?.output_limit ?? context?.output_limit
					)}</span
				>
			</div>
			<div class="flex justify-between gap-4">
				<span class="text-gray-500 dark:text-gray-400">当前分支 / 会话调用</span><span
					class="font-mono">{calls.length} / {$store.calls.length}</span
				>
			</div>
		</div>
		<div class="mt-4 border-t border-gray-100 pt-4 dark:border-white/5">
			<UsageSummary {chatId} compact={true} />
		</div>
		<div
			class="mt-4 flex items-center justify-between border-t border-gray-100 pt-3 text-xs dark:border-white/5"
		>
			<span class="text-gray-400 dark:text-gray-500">自动压缩关闭</span
			>{#if compact && compactionEnabled}<button
					type="button"
					class="rounded-lg px-2 py-1 text-gray-600 hover:bg-black/5 dark:text-gray-300 dark:hover:bg-white/5"
					on:click={compact}>手动压缩…</button
				>{/if}
		</div>
	</div>
</Dropdown>
