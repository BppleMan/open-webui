<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Modal from '$lib/components/common/Modal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DiffEditorModal from '$lib/components/common/DiffEditorModal.svelte';
	import BookOpen from '$lib/components/icons/BookOpen.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { user } from '$lib/stores';
	import { readingFor, type ReadingPosition } from '$lib/stores/chatReading';
	import { modelLabel } from '$lib/utils/modelDirectory';
	import { getOutputText } from './Messages/structuredOutput';
	export let chatId = '';
	export let history: any;
	export let onNavigate: (id: string, offset?: number, adopt?: boolean) => Promise<void>;
	export let currentPosition: () => ReadingPosition | null;
	export let onFork: ((id: string) => Promise<any>) | null = null;
	const i18n = getContext<any>('i18n');
	let open = false,
		tab = 'bookmarks',
		query = '';
	let selected: string[] = [];
	let names: Record<string, string> = {};
	let compare: { a: any; b: any } | null = null;
	$: reading = readingFor($user?.id ?? '', chatId);
	const body = (message: any) => getOutputText(message?.output) || message?.content || '';
	$: messages = Object.values(history?.messages ?? {}).filter(
		(m: any) => m.role === 'user' || m.role === 'assistant'
	) as any[];
	$: replies = messages.filter((m) => m.role === 'assistant' && m.done !== false);
	$: matches = messages
		.filter((m) => !query || body(m).toLocaleLowerCase().includes(query.toLocaleLowerCase()))
		.slice(-100)
		.reverse();
	$: title = (message: any) =>
		$reading.data.branch_names?.[message.id] ||
		(message.role === 'user' ? '用户消息' : modelLabel(message.model));
	async function perform(operation: () => Promise<any>) {
		try {
			await operation();
		} catch (error) {
			toast.error(String(error));
		}
	}
	async function show() {
		open = true;
		await perform(() => reading.load(true));
	}
	async function bookmark(message: any) {
		await perform(() =>
			reading.change((data) => ({
				...data,
				bookmarks: {
					...data.bookmarks,
					[message.id]: {
						label: body(message).slice(0, 48) || title(message),
						excerpt: body(message).slice(0, 200),
						created_at: Date.now()
					}
				}
			}))
		);
	}
	async function navigate(id: string, offset?: number, adopt = false) {
		await perform(async () => {
			await onNavigate(id, offset, adopt);
			open = false;
		});
	}
</script>

<Tooltip content="阅读与分支">
	<button
		type="button"
		aria-label="阅读与分支"
		on:click={show}
		class="flex size-7 items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-100 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
		><BookOpen className="size-5" /></button
	>
</Tooltip>

<Modal bind:show={open} size="lg" ariaLabel="阅读与分支">
	<div class="flex max-h-[85dvh] flex-col p-3 sm:p-5">
		<header class="mb-4 flex items-center justify-between gap-3">
			<h2 class="text-lg font-semibold">阅读与分支</h2>
			<button
				type="button"
				aria-label="关闭阅读面板"
				class="rounded-full p-2 hover:bg-gray-100 dark:hover:bg-gray-800"
				on:click={() => (open = false)}><XMark className="size-5" /></button
			>
		</header>
		<div
			class="mb-4 flex gap-1 rounded-xl bg-gray-50 p-1 text-sm dark:bg-gray-850"
			role="tablist"
			aria-label="阅读工具"
		>
			{#each [['bookmarks', '书签与定位'], ['branches', '分支与对照']] as [id, label]}
				<button
					role="tab"
					aria-selected={tab === id}
					on:keydown={(event) => {
						if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
						event.preventDefault();
						tab = tab === 'bookmarks' ? 'branches' : 'bookmarks';
						event.currentTarget.parentElement
							?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
							[tab === 'bookmarks' ? 0 : 1]?.focus();
					}}
					class="flex-1 rounded-lg px-3 py-2 {tab === id
						? 'bg-white font-medium shadow-xs dark:bg-gray-800'
						: 'text-gray-500'}"
					on:click={() => (tab = id)}>{label}</button
				>
			{/each}
		</div>
		{#if $reading.error}<p role="alert" class="mb-3 text-sm text-red-500">{$reading.error}</p>{/if}
		<div class="min-h-0 overflow-y-auto">
			{#if tab === 'bookmarks'}
				<div class="mb-4 flex flex-wrap gap-2">
					{#if $reading.data.read_position && history.messages[$reading.data.read_position.message_id]}
						<button
							class="rounded-xl bg-gray-900 px-3 py-2 text-sm text-white dark:bg-gray-100 dark:text-gray-900"
							on:click={() =>
								navigate(
									$reading.data.read_position!.message_id,
									$reading.data.read_position!.offset
								)}>继续上次阅读</button
						>
					{/if}
					<button
						class="rounded-xl bg-gray-100 px-3 py-2 text-sm dark:bg-gray-800"
						on:click={() => {
							const position = currentPosition();
							if (position)
								void perform(() =>
									reading.change((data) => ({ ...data, read_position: position }))
								);
						}}>记住当前位置</button
					>
				</div>
				<div class="mb-5 space-y-2">
					{#each Object.entries($reading.data.bookmarks ?? {}) as [id, mark]}
						<div class="flex items-start gap-2 rounded-xl bg-gray-50 p-3 dark:bg-gray-850">
							<button
								class="min-w-0 flex-1 text-start"
								disabled={!history.messages[id]}
								on:click={() => navigate(id)}
								><span class="block text-sm font-medium">{mark.label}</span
								>{#if mark.excerpt !== mark.label || !history.messages[id]}<span
										class="mt-1 block line-clamp-2 text-xs text-gray-500"
										>{history.messages[id] ? mark.excerpt : '原消息已删除'}</span
									>{/if}</button
							>
							<button
								aria-label={`移除书签：${mark.label}`}
								class="rounded-lg p-1.5 text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-800"
								on:click={() =>
									perform(() =>
										reading.change((data) => {
											delete data.bookmarks?.[id];
											return data;
										})
									)}><XMark className="size-4" /></button
							>
						</div>
					{:else}<p class="py-3 text-sm text-gray-500">尚无书签。在下方找到消息即可添加。</p>{/each}
				</div>
				<input
					aria-label="搜索本会话原文"
					placeholder="搜索本会话原文"
					bind:value={query}
					class="mb-3 w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none focus:border-gray-400 dark:border-gray-700"
				/>
				<ul class="divide-y divide-gray-100 dark:divide-gray-800">
					{#each matches as message (message.id)}
						<li class="flex items-center gap-3 py-3">
							<button class="min-w-0 flex-1 text-start" on:click={() => navigate(message.id)}
								><span class="block truncate text-xs text-gray-500">{title(message)}</span><span
									class="mt-1 block line-clamp-2 text-sm">{body(message)}</span
								></button
							><button
								class="shrink-0 rounded-lg px-2 py-1.5 text-xs hover:bg-gray-100 dark:hover:bg-gray-800"
								disabled={!!$reading.data.bookmarks?.[message.id]}
								on:click={() => bookmark(message)}
								>{$reading.data.bookmarks?.[message.id] ? '已收藏' : '添加书签'}</button
							>
						</li>
					{/each}
				</ul>
			{:else}
				<div class="mb-3 flex items-center justify-between gap-3">
					<p class="text-xs text-gray-500">选择两条回复对照；选用分支保留其他候选与费用记录。</p>
					<button
						disabled={selected.length !== 2}
						class="shrink-0 rounded-lg bg-gray-900 px-3 py-2 text-sm text-white disabled:opacity-40 dark:bg-gray-100 dark:text-gray-900"
						on:click={() =>
							(compare = { a: history.messages[selected[0]], b: history.messages[selected[1]] })}
						>对照所选回复</button
					>
				</div>
				<ul class="divide-y divide-gray-100 dark:divide-gray-800">
					{#each [...replies].reverse() as reply (reply.id)}
						<li class="py-3">
							<div class="flex items-start gap-3">
								<input
									type="checkbox"
									aria-label={`对照 ${title(reply)}`}
									checked={selected.includes(reply.id)}
									disabled={!selected.includes(reply.id) && selected.length === 2}
									on:change={(event) =>
										(selected = event.currentTarget.checked
											? [...selected, reply.id]
											: selected.filter((id) => id !== reply.id))}
									class="mt-1 self-start! shrink-0"
								/>
								<div class="min-w-0 flex-1">
									{#if $reading.data.branch_names?.[reply.id]}<div class="mb-1 text-sm font-medium">
											{$reading.data.branch_names[reply.id]}
										</div>{/if}
									<div class="truncate text-xs text-gray-500" title={modelLabel(reply.model)}>
										{modelLabel(reply.model)}
									</div>
									<div class="mt-1 line-clamp-3 text-sm">{body(reply)}</div>
									<div class="mt-2 flex flex-wrap items-center gap-2">
										<input
											aria-label={`分支名称 ${reply.id}`}
											placeholder="为此分支命名"
											value={names[reply.id] ?? $reading.data.branch_names?.[reply.id] ?? ''}
											on:input={(event) =>
												(names = { ...names, [reply.id]: event.currentTarget.value })}
											maxlength="120"
											class="min-w-0 flex-1 rounded-lg bg-gray-50 px-2 py-1.5 text-xs dark:bg-gray-850"
										/><button
											class="rounded-lg px-2 py-1.5 text-xs hover:bg-gray-100 dark:hover:bg-gray-800"
											on:click={() =>
												perform(() =>
													reading.change((data) => ({
														...data,
														branch_names: {
															...data.branch_names,
															[reply.id]: names[reply.id] ?? data.branch_names?.[reply.id] ?? ''
														}
													}))
												)}>保存名称</button
										>
									</div>
									<div class="mt-2 flex items-center justify-end gap-2">
										<button
											class="rounded-lg bg-gray-100 px-2 py-1.5 text-xs dark:bg-gray-800"
											on:click={() => navigate(reply.id, undefined, true)}>选用此分支</button
										>{#if onFork}<button
												class="rounded-lg px-2 py-1.5 text-xs hover:bg-gray-100 dark:hover:bg-gray-800"
												on:click={() =>
													perform(async () => {
														await onFork!(reply.id);
														open = false;
													})}>分叉会话</button
											>{/if}
									</div>
								</div>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	</div>
</Modal>
{#if compare}<DiffEditorModal
		show={true}
		mode="review"
		title="回复原文对照"
		initialContent={body(compare.a)}
		proposedContent={body(compare.b)}
		leftFile={{ name: title(compare.a), path: '' }}
		rightFile={{ name: title(compare.b), path: '' }}
		onClose={() => (compare = null)}
	/>{/if}
