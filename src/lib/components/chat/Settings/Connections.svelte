<script lang="ts">
	import ConnectionManager from '$lib/components/models/ConnectionManager.svelte';
</script>

<div id="tab-connections" class="flex h-full min-h-0 flex-col text-sm">
	<h2 class="mb-4 text-sm font-medium text-gray-900 dark:text-white">个人连接</h2>
	<div class="min-h-0 flex-1 overflow-y-auto scrollbar-hover pr-1.5">
		<ConnectionManager personal={true} />
		<p class="mt-5 max-w-[48rem] text-xs leading-relaxed text-gray-500">
			服务器连接由 OI
			执行；浏览器直连由当前浏览器执行，需要供应商允许跨域访问。后台任务请选择服务器连接。
		</p>
	</div>
</div>
