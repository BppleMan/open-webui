<script lang="ts">
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { importChats } from '$lib/apis/chats';
	export let show = false;
	export let forms: any[] = [];
	export let importId = '';
	export let onImported: (result: any[]) => Promise<void> = async () => {};
	let active: any[] | null = null,
		rows: { amount: string; kind: string }[] = [];
	let busy = false,
		error = '',
		newBatch = false,
		copyId = '';
	$: if (forms !== active) {
		active = forms;
		rows = forms.map(() => ({ amount: '', kind: 'actual' }));
		error = '';
		newBatch = false;
		copyId = crypto.randomUUID();
	}
	async function submit() {
		if (busy) return;
		busy = true;
		error = '';
		try {
			const payload = forms.map((form, index) => {
				const row = rows[index],
					amount = row.amount.trim();
				if (amount && !/^\d+(\.\d{1,36})?$/.test(amount))
					throw new Error('历史费用应为非负十进制金额；留空表示未知。');
				return {
					...form,
					oi_cost_baseline: {
						actual_usd: row.kind === 'actual' && amount ? amount : null,
						estimated_usd: row.kind === 'estimated' && amount ? amount : null
					}
				};
			});
			const result = await importChats(
				localStorage.token,
				payload,
				newBatch ? `${importId}:${copyId}` : importId
			);
			await onImported(result);
			show = false;
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
		} finally {
			busy = false;
		}
	}
</script>

<Modal bind:show size="md" ariaLabel="导入会话与历史费用">
	<div class="p-4 sm:p-6">
		<div class="mb-2 flex items-center justify-between gap-4">
			<h2 class="text-lg font-semibold">导入会话</h2>
			<button
				type="button"
				aria-label="关闭导入"
				disabled={busy}
				on:click={() => (show = false)}
				class="rounded-lg p-1 hover:bg-black/5 dark:hover:bg-white/5"
				><XMark className="size-5" /></button
			>
		</div>
		<p class="mb-4 text-sm text-gray-500">
			为每个会话填写导入部分的历史总费用。留空表示未知，输入 0
			才表示零费用；旧消息中的费用不会重复累加。
		</p>
		{#if error}<p role="alert" class="mb-3 text-sm text-red-500">{error}</p>{/if}
		<form on:submit|preventDefault={submit}>
			<div class="max-h-[45dvh] space-y-3 overflow-auto">
				{#each forms as form, index}
					<div class="rounded-xl bg-gray-50 p-3 dark:bg-gray-850">
						<p class="mb-2 break-words text-sm font-medium">
							{form.chat?.title || `会话 ${index + 1}`}
						</p>
						<div class="flex flex-wrap items-center gap-2">
							<select
								aria-label={`费用类型 ${index + 1}`}
								bind:value={rows[index].kind}
								disabled={busy}
								class="rounded-lg bg-transparent px-2 py-2 text-sm"
								><option value="actual">实际 USD</option><option value="estimated">估算 USD</option
								></select
							>
							<input
								aria-label={`历史费用 ${index + 1}`}
								bind:value={rows[index].amount}
								inputmode="decimal"
								placeholder="未知"
								disabled={busy}
								class="min-w-0 flex-1 rounded-lg bg-white px-3 py-2 text-sm outline-offset-2 dark:bg-white/5"
							/>
						</div>
					</div>
				{/each}
			</div>
			<p class="mt-4 text-xs text-gray-500">
				同一文件重试会返回已有会话，并保留已保存的费用。导入后可在会话费用明细中修改基数。
			</p>
			<label class="mt-3 flex items-center gap-2 text-xs text-gray-500"
				><input type="checkbox" bind:checked={newBatch} disabled={busy} />作为新的导入批次</label
			>
			<div class="mt-5 flex justify-end gap-2">
				<button
					type="button"
					disabled={busy}
					on:click={() => (show = false)}
					class="rounded-lg px-4 py-2 text-sm hover:bg-black/5 dark:hover:bg-white/5">取消</button
				><button
					type="submit"
					disabled={busy || !forms.length}
					class="rounded-lg bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
					>{busy ? '正在导入…' : `导入 ${forms.length} 个会话`}</button
				>
			</div>
		</form>
	</div>
</Modal>
