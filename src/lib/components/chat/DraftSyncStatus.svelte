<script lang="ts">
	import type { DraftState } from '$lib/utils/drafts';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	export let state: DraftState;
	export let temporary = false;
	export let resolve: (choice: 'local' | 'server', candidate?: any) => void;
	export let retry: () => void;
	let show = false;
	const labels = {
		idle: '输入后自动保存草稿',
		loading: '正在恢复草稿',
		local: '本机已保存',
		syncing: '正在同步草稿',
		saved: '草稿已保存到服务器',
		offline: '草稿等待恢复连接后同步',
		conflict: '草稿在另一端被修改',
		error: '本机草稿保存失败'
	};
	$: label = temporary ? '临时对话 · 草稿仅在本机保存' : labels[state.status];
	$: attention = ['conflict', 'error', 'offline'].includes(state.status);
</script>

{#if state.status !== 'idle'}
	<Dropdown bind:show side="top" align="end" visualViewportAware={true} sideOffset={10}>
		<Tooltip
			content={show ? '' : label}
			allowHTML={false}
			placement="top"
			className="flex items-center"
		>
			<button
				type="button"
				aria-label={label}
				aria-expanded={show}
				class="focus-ring flex size-[1.875rem] items-center justify-center rounded-full transition-colors hover:bg-black/5 dark:hover:bg-white/5 {attention
					? 'text-amber-600 dark:text-amber-400'
					: 'text-gray-400 dark:text-gray-600'}"
			>
				<svg
					class="size-4"
					viewBox="0 0 20 20"
					fill="none"
					stroke="currentColor"
					stroke-width="1.5"
					aria-hidden="true"
					><path
						d="M5.5 15h-1a3.5 3.5 0 0 1-.7-6.9A5.5 5.5 0 0 1 14.5 7a4 4 0 0 1 1 7.9"
						stroke-linecap="round"
					/>{#if attention}<path d="M10 10v3m0 2v.1" stroke-linecap="round" />{:else}<path
							d="m7 13 2 2 4-4"
							stroke-linecap="round"
							stroke-linejoin="round"
						/>{/if}</svg
				>
			</button>
		</Tooltip>
		<div
			slot="content"
			class="w-72 max-w-[calc(100vw-1.5rem)] space-y-3 rounded-2xl border border-gray-200/70 bg-white p-4 text-xs text-gray-600 shadow-xl dark:border-white/10 dark:bg-gray-850 dark:text-gray-300"
		>
			<p class="font-medium">{label}</p>
			{#if state.status === 'offline' || state.status === 'error'}<button
					type="button"
					class="rounded-lg bg-gray-100 px-3 py-1.5 dark:bg-white/5"
					on:click={retry}>重试保存</button
				>{/if}
			{#if state.status === 'conflict'}
				<div>
					<p class="text-gray-400">本机输入</p>
					<pre class="my-2 max-h-28 overflow-auto whitespace-pre-wrap">{state.local?.prompt ??
							''}</pre>
					<button type="button" class="underline" on:click={() => resolve('local')}
						>使用本机内容</button
					>
				</div>
				<div class="border-t border-gray-100 pt-3 dark:border-white/5">
					<p class="text-gray-400">服务器草稿</p>
					<pre class="my-2 max-h-28 overflow-auto whitespace-pre-wrap">{state.server?.prompt ??
							''}</pre>
					<button type="button" class="underline" on:click={() => resolve('server')}
						>使用服务器内容</button
					>
				</div>
				{#each state.candidates ?? [] as candidate}<button
						type="button"
						class="block text-left underline"
						on:click={() => resolve('local', candidate)}
						>恢复本机版本 · {new Date(candidate.updatedAt).toLocaleString()}</button
					>{/each}
			{:else}<p class="leading-relaxed text-gray-400 dark:text-gray-500">
					{temporary
						? '可在当前设备恢复；不会创建服务器草稿。'
						: '每个会话独立保存，登录其他浏览器后可继续输入。'}
				</p>{/if}
		</div>
	</Dropdown>
	<span class="sr-only" aria-live="polite">{label}</span>
{/if}
