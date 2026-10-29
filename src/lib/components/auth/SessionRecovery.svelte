<script lang="ts">
	import Modal from '$lib/components/common/Modal.svelte';
	import { sessionRecoveryRequired } from '$lib/utils/session';
</script>

{#if $sessionRecoveryRequired}
	<Modal show={true} size="sm" ariaLabel="恢复登录" onRequestClose={() => {}}>
		<div
			class="flex h-[min(34rem,90dvh)] w-full flex-col overflow-hidden rounded-4xl bg-white dark:bg-gray-900"
		>
			<div class="px-5 pt-4 pb-2">
				<h2 class="font-medium">恢复登录</h2>
				<p class="mt-1 text-sm text-gray-500">
					登录状态已失效，请重新登录。当前页面和未发送内容会保留。
				</p>
				<a
					href="/auth?reauth=1"
					target="_blank"
					rel="noopener noreferrer"
					class="mt-2 inline-block text-sm underline">也可以在新窗口登录</a
				>
			</div>
			<iframe
				title="登录验证"
				src="/auth?reauth=1&embedded=1"
				class="min-h-0 w-full flex-1 border-0"
			></iframe>
		</div>
	</Modal>
{/if}
