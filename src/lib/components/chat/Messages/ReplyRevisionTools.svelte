<script lang="ts">
	import { oiApi } from '$lib/apis/customization';
	import DiffEditorModal from '$lib/components/common/DiffEditorModal.svelte';
	import { getOutputText } from './structuredOutput';
	export let chatId = '';
	export let compact = false;
	export let messageId = '';
	export let temporary = false;
	export let original: any;
	export let draft: any;
	export let onLoad: (value: any) => void;
	let expanded = false;
	let loading = false;
	let error = '';
	let revisions: any[] = [];
	let comparison: { before: string; after: string; title: string } | null = null;
	let includeStructure = false;
	const text = (value: any) =>
		includeStructure && value?.output?.length
			? JSON.stringify(value.output, null, 2)
			: getOutputText(value?.output) || value?.content || '';
	const compare = (before: any, after: any, title: string) => {
		comparison = { before: text(before), after: text(after), title };
	};
	async function showHistory() {
		expanded = !expanded;
		if (!expanded) return;
		loading = true;
		error = '';
		try {
			revisions = await oiApi(`/chats/${chatId}/messages/${messageId}/revisions`);
		} catch (cause) {
			error = String(cause);
		} finally {
			loading = false;
		}
	}
</script>

<div
	class="shrink-0 text-xs {compact
		? 'mb-2 border-b border-gray-100 pb-1 dark:border-gray-800'
		: 'mb-3 rounded-2xl border border-gray-100 p-3 dark:border-gray-800'}"
>
	<div
		class="flex items-center {compact ? 'gap-1' : 'flex-wrap gap-2'}"
		data-testid="reply-revision-toolbar"
	>
		<button
			type="button"
			class="rounded-lg px-2 py-1 font-medium hover:bg-gray-100 dark:hover:bg-gray-800"
			aria-label="对照修改"
			title="对照修改"
			on:click={() => compare(original, draft, '对照本次修改')}
			>{#if compact}<span class="sm:hidden">对照</span><span class="hidden sm:inline">对照修改</span
				>{:else}对照修改{/if}</button
		>
		<button
			type="button"
			class="rounded-lg px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-800"
			aria-label="撤销本次修改"
			title="撤销本次修改"
			on:click={() => onLoad(structuredClone(original))}
			>{#if compact}<span class="sm:hidden">撤销</span><span class="hidden sm:inline"
					>撤销本次修改</span
				>{:else}撤销本次修改{/if}</button
		>
		{#if !temporary}
			<button
				type="button"
				aria-expanded={expanded}
				class="rounded-lg px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-800"
				aria-label="修订历史"
				title="修订历史"
				on:click={showHistory}
				>{#if compact}<span class="sm:hidden">历史</span><span class="hidden sm:inline"
						>修订历史</span
					>{:else}修订历史{/if}</button
			>
		{/if}
		<label
			class="ms-auto flex shrink-0 items-center gap-1 whitespace-nowrap text-gray-500 dark:text-gray-400"
			><input
				type="checkbox"
				aria-label="对照完整内容"
				bind:checked={includeStructure}
			/>{#if compact}<span class="sm:hidden">完整内容</span><span class="hidden sm:inline"
					>对照完整内容</span
				>{:else}对照完整内容{/if}</label
		>
	</div>
	{#if expanded}
		<div class="mt-2 border-t border-gray-100 pt-2 dark:border-gray-800">
			<p class="mb-2 text-gray-500 dark:text-gray-400">
				载入旧版本后仍需保存。保存会保留当前版本，费用记录不变。
			</p>
			{#if loading}<p role="status">正在读取修订…</p>
			{:else if error}<p role="alert" class="text-red-500">{error}</p>
			{:else if !revisions.length}<p class="text-gray-500">尚无已保存的修订。</p>
			{:else}
				<div class="max-h-44 overflow-auto divide-y divide-gray-100 dark:divide-gray-800">
					{#each revisions as revision, index (revision.id)}
						<div class="flex flex-wrap items-center gap-2 py-2">
							<span class="min-w-0 flex-1"
								>修订 {revisions.length - index} · {new Date(
									revision.created_at
								).toLocaleString()}</span
							>
							<button
								type="button"
								class="rounded-lg px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-800"
								on:click={() => compare(revision, draft, '旧版本与当前编辑')}>对照</button
							>
							<button
								type="button"
								class="rounded-lg bg-gray-100 px-2 py-1.5 dark:bg-gray-800"
								on:click={() => onLoad(structuredClone(revision))}>载入此版本</button
							>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	{/if}
</div>

{#if comparison}
	<DiffEditorModal
		show={true}
		mode="review"
		title={comparison.title}
		initialContent={comparison.before}
		proposedContent={comparison.after}
		leftFile={{ name: '原版本', path: '' }}
		rightFile={{ name: '当前编辑', path: '' }}
		onClose={() => (comparison = null)}
	/>
{/if}
