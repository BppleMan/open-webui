<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { oiApi } from '$lib/apis/customization';
	export let connection: any;
	export let protocol: string;
	export let model: string | undefined = undefined;
	export let settingsText: string | undefined = undefined;
	export let extraSettings: Record<string, any> = {};
	let mounted = false,
		sequence = 0,
		previousKey = '';
	let timer: ReturnType<typeof setTimeout>;
	let preview: any = null,
		error = '',
		loading = false;
	onMount(() => {
		mounted = true;
	});
	onDestroy(() => {
		clearTimeout(timer);
		sequence++;
	});
	$: if (mounted) queue(connection, protocol, model, settingsText, extraSettings);
	function queue(
		c: any,
		mode: string,
		upstream: string | undefined,
		text: string | undefined,
		extra: any
	) {
		const key = JSON.stringify([
			c?.base_url,
			c?.template,
			c?.settings,
			mode,
			upstream,
			text,
			extra
		]);
		if (key === previousKey) return;
		previousKey = key;
		clearTimeout(timer);
		const current = ++sequence;
		preview = null;
		error = '';
		loading = false;
		if (!c?.base_url || !mode) return;
		let options;
		try {
			options = text == null ? {} : JSON.parse(text);
		} catch {
			error = '连接选项不是有效的 JSON';
			return;
		}
		loading = true;
		timer = setTimeout(async () => {
			try {
				const result = await oiApi('/connections/resolve', 'POST', {
					name: c.name || 'preview',
					template: c.template,
					base_url: c.base_url,
					protocol: mode,
					settings: { ...c.settings, ...options, ...extra },
					...(upstream ? { model: upstream } : {})
				});
				if (current === sequence) preview = result;
			} catch (e) {
				if (current === sequence) error = String(e);
			} finally {
				if (current === sequence) loading = false;
			}
		}, 200);
	}
</script>

<div
	class="space-y-1 text-[0.6875rem] leading-relaxed text-gray-500 dark:text-gray-400"
	aria-live="polite"
	data-testid="connection-endpoint-preview"
>
	{#if preview}
		<div>实际请求地址</div>
		<div class="break-all font-mono text-gray-600 dark:text-gray-300">{preview.generate_url}</div>
		{#if preview.stream_url !== preview.generate_url}<details>
				<summary class="w-fit cursor-pointer">流式地址</summary>
				<div class="break-all font-mono">{preview.stream_url}</div>
			</details>{/if}
		{#if preview.needs_review}<p class="text-amber-700 dark:text-amber-400">
				旧地址未自动拆分，当前保留原有路由。请核对后选择自动路径规则。
			</p>{/if}
	{:else if error}<p class="text-amber-700 dark:text-amber-400">{error}</p>
	{:else}<span>{loading ? '正在解析地址…' : '填写服务地址后预览请求路径'}</span>{/if}
</div>
