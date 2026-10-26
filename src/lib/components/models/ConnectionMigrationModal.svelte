<script lang="ts">
	import { onMount } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import { oiApi } from '$lib/apis/customization';
	export let scope: 'personal' | 'server' = 'personal';
	export let onClose = () => {};
	export let onApplied: () => Promise<void> = async () => {};
	let preview: any = null,
		busy = false,
		error = '',
		result: any = null;
	let choices: Record<string, string> = {};
	const issues: Record<string, string> = {
		invalid_legacy_url: '旧地址需要手动修正',
		missing_realtime_model: '请先补齐实时语音与转录模型',
		retain_existing_engine: '保留现有引擎配置，不转换为不兼容的接口',
		unsupported_transcription_request_format: '此转录格式需先手动配置兼容连接'
	};
	async function refresh() {
		busy = true;
		error = '';
		result = null;
		try {
			preview = await oiApi('/connections/migration/preview', 'POST', { scope });
			choices = Object.fromEntries(
				preview.entries.map((entry: any) => [
					entry.id,
					entry.suggested_action === 'review' ? 'skip' : entry.suggested_action
				])
			);
		} catch (cause: any) {
			error = cause.message;
		} finally {
			busy = false;
		}
	}
	async function apply() {
		busy = true;
		error = '';
		try {
			result = await oiApi('/connections/migration/apply', 'POST', {
				scope,
				version: preview.version,
				choices: preview.entries
					.filter((entry: any) => choices[entry.id] !== 'skip')
					.map((entry: any) => ({
						source_id: entry.id,
						action: choices[entry.id],
						connection_id: choices[entry.id] === 'reuse' ? entry.existing_connection.id : null,
						acknowledge_duplicate: entry.ambiguous
					}))
			});
			await onApplied();
		} catch (cause: any) {
			error = cause.message;
		} finally {
			busy = false;
		}
	}
	onMount(refresh);
</script>

<Modal show size="md" ariaLabel="迁移旧连接" onRequestClose={onClose}>
	<div class="max-h-[82dvh] overflow-auto p-5">
		<div class="flex items-center justify-between gap-4">
			<h2 class="text-base font-semibold">迁移旧连接</h2>
			<button
				type="button"
				class="rounded-lg px-2 py-1 text-xs text-gray-500 hover:bg-black/5 dark:hover:bg-white/5"
				on:click={onClose}>关闭</button
			>
		</div>
		<p class="mt-2 text-xs leading-relaxed text-gray-500">
			先核对来源，再应用映射。已有连接的名称、参数与价格保留；浏览器直连继续由浏览器执行。列表不显示凭据。
		</p>
		{#if error}<p role="alert" class="mt-3 text-sm text-red-500">{error}</p>
			<button type="button" class="mt-2 text-xs underline" on:click={refresh} disabled={busy}
				>重新读取预览</button
			>{/if}
		{#if result}
			{#each result.warnings || [] as warning}<p role="alert" class="my-2 text-xs text-amber-600">
					{warning}
				</p>{/each}
			<p role="status" class="my-4 text-sm">
				已迁移 {result.connections.length} 个来源；{result.retained_sources.length} 个来源保留原配置。
			</p>
			{#if Object.values(result.model_mapping).some((ids: any) => ids.length > 1)}<p
					class="text-xs text-amber-600"
				>
					部分旧模型 ID 对应多个连接。历史引用继续保留，使用前请手动选择具体连接。
				</p>{/if}
			<button
				type="button"
				class="mt-4 rounded-lg bg-gray-900 px-3 py-2 text-xs text-white dark:bg-gray-100 dark:text-gray-900"
				on:click={onClose}>完成</button
			>
		{:else if preview}
			<div class="mt-4 divide-y divide-gray-100 dark:divide-white/5">
				{#each preview.entries as entry (entry.id)}
					<div class="space-y-2 py-3">
						<div class="text-sm font-medium">{entry.name}</div>
						<div class="break-all text-xs text-gray-500">
							{entry.transport === 'browser' ? '浏览器直连' : '服务器'} · {entry.protocol}{entry.base_url
								? ' · ' + entry.base_url
								: ''}
						</div>
						<div class="text-xs text-gray-500">
							{entry.models.length
								? `${entry.models.length} 个已配置模型`
								: '模型将在迁移或刷新时发现'}
						</div>
						{#if entry.issue}<p class="text-xs text-amber-600">
								{issues[entry.issue] || entry.issue}
							</p>{:else}
							{#if entry.ambiguous}<p class="text-xs text-amber-600">
									存在相同来源，请逐项确认是否新建独立连接。
								</p>{/if}
							<label class="block text-xs text-gray-500"
								>迁移方式
								<select
									aria-label={`迁移 ${entry.id}`}
									bind:value={choices[entry.id]}
									disabled={busy}
									class="mt-1 w-full rounded-lg border border-gray-100 bg-transparent p-2 dark:border-white/10"
								>
									<option value="skip">暂不迁移</option>
									<option value="create">新建独立连接</option>
									{#if entry.existing_connection}<option value="reuse"
											>沿用 {entry.existing_connection.name}，保留自定义配置</option
										>{/if}
								</select>
							</label>
						{/if}
					</div>
				{:else}<p class="py-5 text-sm text-gray-500">没有待迁移的旧配置。</p>{/each}
			</div>
			<div class="mt-4 flex justify-end gap-3">
				<button type="button" class="text-xs text-gray-500" on:click={onClose} disabled={busy}
					>取消</button
				>
				<button
					type="button"
					class="rounded-lg bg-gray-900 px-3 py-2 text-xs text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
					on:click={apply}
					disabled={busy || !Object.values(choices).some((value) => value !== 'skip')}
					>{busy ? '迁移中…' : '应用迁移'}</button
				>
			</div>
		{:else}<p class="py-5 text-sm text-gray-500">读取配置预览…</p>{/if}
	</div>
</Modal>
