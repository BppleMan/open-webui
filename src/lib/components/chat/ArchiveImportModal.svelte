<script lang="ts">
	import Modal from '$lib/components/common/Modal.svelte';
	import ModelOptions from '$lib/components/models/ModelOptions.svelte';
	import { models } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	export let show = false;
	export let file: File | null = null;
	export let onImported: (result: any) => Promise<void> = async () => {};
	let active: File | null = null;
	let preview: any = null;
	let busy = false,
		error = '',
		importId = '';
	let mapping: Record<string, string> = {};
	let previewVersion = 0;
	async function request(path: string, form: FormData) {
		const response = await fetch(`${WEBUI_API_BASE_URL}${path}`, {
			method: 'POST',
			headers: { Authorization: `Bearer ${localStorage.token}` },
			body: form
		});
		const data = await response.json();
		if (!response.ok) throw new Error(data.detail || '归档操作失败');
		return data;
	}
	async function inspect(selected: File) {
		const version = ++previewVersion;
		busy = true;
		error = '';
		preview = null;
		mapping = {};
		importId = crypto.randomUUID();
		try {
			const form = new FormData();
			form.append('file', selected);
			const result = await request('/archives/preview', form);
			if (version === previewVersion && active === selected) preview = result;
		} catch (cause) {
			if (version === previewVersion) error = String(cause);
		} finally {
			if (version === previewVersion) busy = false;
		}
	}
	$: if (file !== active) {
		active = file;
		if (file) void inspect(file);
		else {
			previewVersion++;
			preview = null;
			busy = false;
		}
	}
	async function restore() {
		if (!file || busy) return;
		busy = true;
		error = '';
		try {
			const form = new FormData();
			form.append('file', file);
			form.append('import_id', importId);
			form.append(
				'model_mapping',
				JSON.stringify(Object.fromEntries(Object.entries(mapping).filter(([, value]) => value)))
			);
			const result = await request('/archives/import', form);
			await onImported(result);
			show = false;
		} catch (cause) {
			error = String(cause);
		} finally {
			busy = false;
		}
	}
</script>

<Modal bind:show size="md" ariaLabel="还原 OI 会话归档">
	<div class="max-h-[85dvh] overflow-auto p-4 sm:p-6">
		<h2 class="mb-2 text-lg font-semibold">还原会话归档</h2>
		<p class="mb-4 text-sm text-gray-500">{file?.name}</p>
		{#if error}<p role="alert" class="mb-4 text-sm text-red-500">{error}</p>{/if}
		{#if busy && !preview}<p role="status" class="py-8 text-center text-sm text-gray-500">
				正在检查归档与附件…
			</p>{/if}
		{#if preview}
			<div class="mb-5 rounded-2xl bg-gray-50 p-4 dark:bg-gray-850">
				<div class="font-medium">{preview.title || '未命名会话'}</div>
				<div class="mt-2 text-sm text-gray-500">
					{preview.message_count} 条消息 · {preview.call_count} 次原调用 · {preview.revision_count} 条修订
				</div>
				<div class="mt-1 text-sm text-gray-500">
					{preview.attachments.filter((asset: any) => asset.status === 'included').length} 个附件可还原
				</div>
			</div>
			{#if preview.attachments.some((asset: any) => asset.status !== 'included') || preview.external_reference_count}
				<p
					class="mb-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-900/20 dark:text-amber-300"
				>
					归档中有未打包附件或外部链接，仍保留原引用；这些内容无法随归档离线还原。
				</p>
			{/if}
			{#if preview.attachments.some((asset: any) => asset.status === 'included')}<p
					class="mb-4 text-xs text-gray-500"
				>
					附件原文件与已提取文本会还原。检索索引不在归档中，若需要检索，请重新处理附件。
				</p>{/if}
			{#if preview.model_references.length}
				<h3 class="mb-3 text-sm font-medium">模型对应关系</h3>
				<div class="mb-5 space-y-3">
					{#each preview.model_references as reference}
						<label class="block"
							><span class="mb-1.5 block text-sm"
								>{reference.name || reference.id}<span class="ms-2 text-xs text-gray-500"
									>{reference.connection?.name || ''}</span
								></span
							><select
								disabled={busy}
								bind:value={mapping[reference.id]}
								class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm dark:border-gray-700"
								><option value=""
									>{reference.available ? '保留原模型' : '保留历史标识，稍后选择可用模型'}</option
								><ModelOptions items={$models} /></select
							></label
						>
					{/each}
				</div>
			{/if}
			<p class="text-xs leading-relaxed text-gray-500">
				还原为新会话，保留分支、修订、书签及原费用记录。重复导入相同调用不会重复计入整体费用。
			</p>
		{/if}
		<div class="mt-5 flex justify-end gap-2">
			<button
				type="button"
				disabled={busy}
				class="rounded-xl px-4 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
				on:click={() => (show = false)}>取消</button
			><button
				type="button"
				disabled={busy || !preview}
				class="rounded-xl bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-40 dark:bg-gray-100 dark:text-gray-900"
				on:click={restore}>{busy ? '正在还原…' : '还原为新会话'}</button
			>
		</div>
	</div>
</Modal>
