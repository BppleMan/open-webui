<script lang="ts">
	import { onDestroy } from 'svelte';
	import { usageFor } from '$lib/stores/oiUsage';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UsageSummary from './UsageSummary.svelte';
	import {
		contextBudget,
		contextProjection,
		contextOccupancy,
		tokenSubtotal
	} from '$lib/utils/contextWindow';
	export let chatId = '',
		currentId: string | null = null,
		history: any = { messages: {} },
		model: any = null;
	export let compact: (() => void) | undefined = undefined;
	export let compactionEnabled = false;
	export let draft = '',
		system = '',
		reservedOutput: number | null = null,
		compactionThreshold: number | null = null,
		compactionState = 'idle';
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
	$: calls = [...new Map($store.calls.map((c) => [c.id, c])).values()].filter(
		(c) =>
			generationIds.has(c.generation_id) && (!model || (c.view_model_id ?? c.model_id) === model.id)
	);
	$: contextCalls = calls.filter((c) =>
		['assistant', 'tool_followup'].includes(c.usage?.context?.purpose)
	);
	$: latest = contextCalls.at(-1) || calls.at(-1);
	$: context = latest?.usage?.context;
	$: budget = contextBudget(model?.capabilities, reservedOutput);
	$: limit = budget.effective;
	$: projection = contextProjection(history, currentId, draft, system, compactionEnabled);
	$: occupancy = contextOccupancy(projection.count, context);
	$: count = occupancy.total;
	$: percent =
		limit == null ? null : limit === 0 ? 100 : Math.max(0, Math.min(100, (count / limit) * 100));
	$: remaining = limit != null ? Math.max(0, limit - count) : null;
	$: turnsLeft =
		remaining != null && projection.averageTurn && !projection.mediaUnknown
			? Math.floor(remaining / projection.averageTurn)
			: null;
	$: source = '当前文本 + 最近工具定义估算 · 媒体另计';
	$: usageRows = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'].map(
		(key) => ({ key, branch: tokenSubtotal(calls, key), session: tokenSubtotal($store.calls, key) })
	);
	$: subagentUsage = tokenSubtotal(
		$store.calls.filter((c) => c.usage?.context?.purpose === 'subagent'),
		'input_tokens'
	);
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
	contentClass="w-[23rem] max-w-[calc(100vw-1.5rem)] overflow-y-auto overscroll-contain rounded-2xl border border-gray-200/70 bg-white shadow-xl dark:border-white/10 dark:bg-gray-850"
	onOpenChange={(open) => {
		if (open) void store.refresh();
	}}
>
	<Tooltip
		content={show ? '' : label}
		placement="top"
		allowHTML={false}
		className="flex items-center"
	>
		<button
			type="button"
			aria-label={label}
			aria-expanded={show}
			aria-haspopup="dialog"
			data-testid="context-window-trigger"
			class="focus-ring flex size-[1.875rem] shrink-0 items-center justify-center rounded-full text-gray-500 transition-colors hover:bg-black/5 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-gray-200"
		>
			<svg width="20" height="20" viewBox="0 0 20 20" class="-rotate-90" aria-hidden="true">
				<circle
					cx="10"
					cy="10"
					r="8"
					fill="none"
					stroke-width="2.4"
					class={percent == null
						? 'stroke-gray-400 dark:stroke-gray-500'
						: 'stroke-gray-200 dark:stroke-gray-700'}
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
		class="p-4 text-sm text-gray-700 dark:text-gray-200"
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
				<span class="size-2 rounded-full bg-blue-500"></span><span class="flex-1"
					>当前分支文本（估算）</span
				><span class="font-mono tabular-nums">{format(projection.count)}</span>
			</div>
			<div class="flex items-center gap-2">
				<span class="size-2 rounded-full bg-amber-500"></span><span class="flex-1"
					>工具定义（最近请求估算）</span
				><span class="font-mono tabular-nums">{format(occupancy.tools)}</span>
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
		<details class="mt-3 space-y-2 text-xs">
			<summary class="cursor-pointer text-gray-500 dark:text-gray-400">容量与预算明细</summary>
			{#each [['上下文总容量', budget.window], ['提供方输入上限', budget.input], ['本轮预留输出', budget.output], ['输入草稿（估算）', projection.draftTokens], ['摘要（估算）', projection.summaryTokens], ['最近请求实际输入', latest?.usage?.input_tokens], ['预计剩余轮数（文本趋势）', turnsLeft], ['压缩阈值（非窗口上限）', compactionThreshold]] as [name, value]}
				<div class="flex justify-between gap-4">
					<span class="text-gray-500">{name}</span><span class="font-mono"
						>{format(value as number | null)}</span
					>
				</div>
			{/each}
			<p class="text-gray-400">
				工具定义沿用最近请求估算，变更工具后待下次请求校正。媒体与协议开销未计入；实际计数另列。只预警，不自动裁剪。
			</p>
		</details>
		{#if context}
			<details class="mt-4 text-xs">
				<summary class="cursor-pointer text-gray-500 dark:text-gray-400"
					>最近请求占用明细 <span class="text-gray-400 dark:text-gray-500">· 估算</span></summary
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
				{#each [['媒体', context.media_tokens], ['MCP', context.mcp?.tokens], ['Skills', context.skills?.tokens], ['子代理上下文', context.subagents?.tokens]] as [name, value]}
					<div class="mt-2 flex justify-between gap-4">
						<span>{name}</span><span>{format(value as number | null)}</span>
					</div>
				{/each}
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
				<span class="text-gray-500 dark:text-gray-400">最近预留输出 / 模型上限</span><span
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
		<details class="mt-4 border-t border-gray-100 pt-3 text-xs dark:border-white/5">
			<summary class="cursor-pointer">用量小计 · 当前分支 / 全会话</summary>
			{#each usageRows as row}
				<div class="mt-2 flex justify-between gap-4">
					<span
						>{{
							input_tokens: '输入',
							output_tokens: '输出（含推理）',
							cache_read_tokens: '缓存读取',
							cache_write_tokens: '缓存写入'
						}[row.key]}</span
					><span>{format(row.branch.value)} / {format(row.session.value)}</span>
				</div>
				{#if row.session.unknown}<p class="text-gray-400">
						另有 {row.session.unknown} 次调用未提供该字段
					</p>{/if}
			{/each}
			<div class="mt-2 flex justify-between">
				<span>子代理输入（全会话）</span><span>{format(subagentUsage.value)}</span>
			</div>
			<p class="mt-2 text-gray-400">
				按调用 ID 去重；缓存是输入子集，不另加到上下文。缺失值不是零。
			</p>
		</details>
		<div class="mt-4 border-t border-gray-100 pt-4 dark:border-white/5">
			<UsageSummary {chatId} compact={true} />
		</div>
		<div
			class="mt-4 flex items-center justify-between border-t border-gray-100 pt-3 text-xs dark:border-white/5"
		>
			<span class="text-gray-400 dark:text-gray-500"
				>自动压缩关闭 · {compactionState === 'running'
					? '正在手动压缩'
					: compactionState === 'failed'
						? '上次压缩失败，原文保留'
						: projection.summary && compactionEnabled
							? '已使用手动摘要'
							: '完整历史'}</span
			>{#if compact && compactionEnabled}<button
					type="button"
					class="rounded-lg px-2 py-1 text-gray-600 hover:bg-black/5 dark:text-gray-300 dark:hover:bg-white/5"
					disabled={compactionState === 'running'}
					on:click={compact}>手动压缩…</button
				>{/if}
		</div>
	</div>
</Dropdown>
