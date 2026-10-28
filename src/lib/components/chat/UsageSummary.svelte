<script lang="ts">
	import { onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { oiApi } from '$lib/apis/customization';
	import { usageFor, dollars } from '$lib/stores/oiUsage';
	import { modelLabel } from '$lib/utils/modelDirectory';
	export let chatId = '';
	export let compact = false;
	let store = usageFor(chatId),
		disconnect: (() => void) | undefined,
		active = '';
	let open = false,
		baselines: any[] = [],
		baselineRevision = 0,
		saving = false,
		batch = '',
		amount = '',
		kind = 'actual',
		note = '';
	$: if (chatId !== active) {
		disconnect?.();
		active = chatId;
		open = false;
		baselines = [];
		batch = '';
		amount = '';
		note = '';
		baselineRevision = 0;
		store = usageFor(chatId);
		if (chatId) disconnect = store.connect();
	}
	onDestroy(() => disconnect?.());
	const expand = async () => {
		open = !open;
		if (open && !chatId.startsWith('temporary:'))
			try {
				baselines = await oiApi(`/chats/${chatId}/cost-baselines`);
			} catch (e) {
				toast.error(String(e));
			}
	};
	const save = async () => {
		if (saving) return;
		saving = true;
		try {
			baselines = await oiApi(`/chats/${chatId}/cost-baselines`, 'PUT', {
				batch_id: batch || 'imported-history',
				actual_usd: kind === 'actual' && amount !== '' ? amount : null,
				estimated_usd: kind === 'estimated' && amount !== '' ? amount : null,
				note,
				revision: baselineRevision
			});
			baselineRevision =
				baselines.find((item) => item.batch_id === (batch || 'imported-history'))?.revision ?? 0;
			await store.refresh();
			toast.success('历史费用已保存');
		} catch (e: any) {
			toast.error(
				e.status === 409
					? '费用已在其他位置修改，请重新选择该批次后再编辑。当前输入保留。'
					: String(e)
			);
			if (e.status === 409) baselines = await oiApi(`/chats/${chatId}/cost-baselines`);
		} finally {
			saving = false;
		}
	};
</script>

{#if chatId && $store.totals}
	<div
		class={compact
			? 'text-xs text-gray-500 dark:text-gray-400'
			: 'mx-auto w-full max-w-5xl px-4 py-1 text-xs text-gray-500'}
	>
		<button
			type="button"
			class="flex flex-wrap gap-x-4 gap-y-1 text-left"
			on:click={expand}
			aria-expanded={open}
		>
			<span>本会话实际 <strong class="font-mono">{dollars($store.totals.actual_usd)}</strong></span>
			<span>估算 <strong class="font-mono">{dollars($store.totals.estimated_usd)}</strong></span>
			{#if $store.totals.incomplete}<span
					>费用未知：{$store.totals.unknown_calls} 次调用、{$store.totals.unknown_baselines} 批历史</span
				>{/if}
			<span class="underline">费用明细</span>
		</button>
		{#if open}
			<div class="mt-3 space-y-3 rounded-xl bg-gray-50 p-3 dark:bg-white/[0.025]">
				<p>
					累计包含重新生成和工具调用；切换、编辑或删除回复不会删账。实际金额确认后会替换对应估算。
				</p>
				<div class="max-h-56 overflow-auto">
					<table class="w-full text-left">
						<thead><tr><th>调用</th><th>实际</th><th>估算</th><th>状态</th></tr></thead><tbody
							>{#each $store.calls as call (call.id)}<tr
									><td
										class="max-w-48 truncate"
										title={modelLabel(call.binding_id || call.model_id)}
										>{modelLabel(call.binding_id || call.model_id)}</td
									><td>{dollars(call.actual_usd)}</td><td>{dollars(call.estimated_usd)}</td><td
										>{call.status}</td
									></tr
								>{/each}</tbody
						>
					</table>
				</div>
				{#if !chatId.startsWith('temporary:')}
					<details>
						<summary class="cursor-pointer">导入历史费用基数</summary>
						{#each baselines as item}<button
								type="button"
								class="my-2 block underline"
								on:click={() => {
									batch = item.batch_id;
									baselineRevision = item.revision;
									kind = item.actual_usd != null ? 'actual' : 'estimated';
									amount = item.actual_usd ?? item.estimated_usd ?? '';
									note = item.note;
								}}>{item.batch_id} · {dollars(item.actual_usd ?? item.estimated_usd)}</button
							>{/each}
						<div class="mt-2 flex flex-wrap gap-2">
							<input
								aria-label="导入批次"
								placeholder="导入批次"
								bind:value={batch}
								on:input={() => (baselineRevision = 0)}
								class="min-w-0 max-w-full rounded-lg border border-gray-200 bg-transparent p-2 dark:border-white/10"
							/><select
								aria-label="费用类型"
								bind:value={kind}
								class="min-w-0 max-w-full rounded-lg border border-gray-200 bg-transparent p-2 dark:border-white/10"
								><option value="actual">实际 USD</option><option value="estimated">估算 USD</option
								></select
							><input
								aria-label="历史费用"
								placeholder="未知"
								inputmode="decimal"
								bind:value={amount}
								class="w-28 rounded-lg border bg-transparent p-2"
							/><input
								aria-label="基数说明"
								placeholder="说明"
								bind:value={note}
								class="min-w-0 max-w-full rounded-lg border border-gray-200 bg-transparent p-2 dark:border-white/10"
							/><button
								type="button"
								disabled={saving}
								class="rounded-lg bg-gray-900 px-3 py-2 text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
								on:click={save}>{saving ? '正在保存…' : '保存基数'}</button
							>
						</div>
						<button
							type="button"
							class="mt-3 text-gray-500 underline"
							on:click={() => {
								batch = '';
								amount = '';
								note = '';
								baselineRevision = 0;
							}}>新增批次</button
						>
						<p class="mt-2">同批次再次保存是替换；留空表示未知，明确输入 0 才表示零费用。</p>
					</details>
				{/if}
			</div>
		{/if}
	</div>
{/if}
