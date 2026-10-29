<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { models, user } from '$lib/stores';
	import { getModels } from '$lib/apis';
	import { oiApi } from '$lib/apis/customization';
	import { discoverBrowserModels } from '$lib/utils/browserConnections';
	import ConnectionMigrationModal from './ConnectionMigrationModal.svelte';
	import ConnectionEndpointPreview from './ConnectionEndpointPreview.svelte';
	import { modelRanks } from '$lib/utils/connectionManagement';
	import Modal from '$lib/components/common/Modal.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';
	import AdminSettingSection from '$lib/components/admin/Settings/AdminSettingSection.svelte';
	import Cog6 from '$lib/components/icons/Cog6.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import ArrowPath from '$lib/components/icons/ArrowPath.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	let connections: any[] = [],
		bindings: any[] = [],
		templates: any[] = [],
		protocolDirectory: any[] = [];
	let ranks = new Map<string, number>();
	let rankedSearch = '';
	$: if (search !== rankedSearch) {
		ranks = modelRanks(bindings);
		rankedSearch = search;
	}
	let search = '',
		busy = false,
		loading = true,
		loadError = '',
		chooseTemplate = false,
		templateSearch = '';
	let expanded = new Set<string>();
	export let personal = false;
	let showMigration = false;
	let showRemoved = false;
	const inputClass =
		'w-full min-w-0 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2.5 py-2 text-sm text-gray-700 outline-hidden transition-colors focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-200 dark:focus:border-blue-500';
	const iconClass =
		'focus-ring flex size-7 shrink-0 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-black/5 hover:text-gray-700 disabled:opacity-40 dark:text-gray-500 dark:hover:bg-white/5 dark:hover:text-gray-200';
	const primaryClass =
		'rounded-lg bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white';
	$: protocolLabels = Object.fromEntries(protocolDirectory.map((p) => [p.id, p.label]));
	const templateName = (connection: any) =>
		templates.find((t) => t.id === connection.template)?.name ||
		protocolLabels[connection.protocol] ||
		connection.template;
	const toggle = (id: string) => {
		const next = new Set(expanded);
		if (next.has(id)) next.delete(id);
		else {
			next.add(id);
			ranks = modelRanks(bindings, ranks, id);
		}
		expanded = next;
	};
	const templateDescription = (template: any) =>
		template.description || protocolLabels[template.protocol];
	const selectedTemplate = (connection: any) =>
		templates.find((t) => t.id === connection?.template);
	const manualModels = (connection: any) =>
		connection.settings?.routing?.profile === 'azure-openai' &&
		connection.protocol === 'openai-chat';
	const templateFieldsFor = (connection: any) =>
		(selectedTemplate(connection)?.fields || []).filter(
			(f: any) => !f.protocols || f.protocols.includes(connection.protocol)
		);

	let connectionEdit: any = null,
		modelEdit: any = null;
	$: modelConnection = connections.find((c) => c.id === modelEdit?.connection_id);
	let originalCapabilities: Record<string, any> = {};
	let customHeaders = '';
	let apiKey = '',
		nativeSettings = '{}',
		connectionAuth = 'bearer',
		templateFields: Record<string, string> = {},
		customParams = '{}';
	let reasoningMode = 'levels';
	let budgetMin: number | undefined,
		budgetMax: number | undefined,
		budgetStep: number | undefined,
		budgetDefault: number | undefined;
	let levels = '',
		reasoningParameter = '',
		reasoningDefault = '';
	const capabilities = [
		'chat',
		'vision',
		'tools',
		'image_generation',
		'image_edit',
		'tts',
		'stt',
		'realtime',
		'embedding',
		'rerank'
	];
	const rates = [
		'input',
		'output',
		'cache_read',
		'cache_write',
		'cache_write_5m',
		'cache_write_1h',
		'reasoning',
		'input_audio',
		'output_audio',
		'cache_read_audio',
		'input_image',
		'output_image',
		'cache_read_image'
	];
	const load = async (reorder = true) => {
		loadError = '';
		try {
			[connections, bindings, templates, protocolDirectory] = await Promise.all([
				oiApi('/connections?include_deleted=true'),
				oiApi('/model-bindings'),
				oiApi('/connections/templates'),
				oiApi('/protocols')
			]);
			if (reorder) ranks = modelRanks(bindings);
		} catch (e) {
			loadError = String(e);
		} finally {
			loading = false;
		}
	};
	onMount(() => {
		void load();
	});
	const refresh = async (reorder = true) => {
		await load(reorder);
		models.set(await getModels(localStorage.token));
	};
	const run = async (action: () => Promise<any>, reorder = true) => {
		if (busy) return;
		busy = true;
		try {
			await action();
			await refresh(reorder);
		} catch (e) {
			toast.error(String(e));
		} finally {
			busy = false;
		}
	};
	const create = (template: any) => {
		chooseTemplate = false;
		connectionEdit = {
			name: uniqueConnectionName(template.name),
			template: template.id,
			protocol: template.protocol,
			transport: 'server',
			base_url: template.base_url,
			shared: !personal && $user?.role === 'admin',
			enabled: true,
			settings: { ...template.settings, auth: template.auth }
		};
		initializeConnectionOptions(connectionEdit);
	};
	const editConnection = (connection: any) => {
		connectionEdit = structuredClone(connection);
		initializeConnectionOptions(connection);
	};
	const uniqueConnectionName = (base: string) => {
		let name = base,
			suffix = 2;
		while (connections.some((c) => c.name === name)) name = `${base} ${suffix++}`;
		return name;
	};
	const initializeConnectionOptions = (connection: any) => {
		apiKey = '';
		customHeaders = '';
		const settings = { ...(connection.settings || {}) };
		connectionAuth = settings.auth || selectedTemplate(connection)?.auth || 'bearer';
		delete settings.auth;
		templateFields = {};
		for (const field of selectedTemplate(connection)?.fields || []) {
			templateFields[field.key] = settings[field.key] || '';
			delete settings[field.key];
		}
		delete settings.template_version;
		delete settings.routing;
		nativeSettings = JSON.stringify(settings, null, 2);
	};
	const saveConnection = () =>
		run(async () => {
			const options = JSON.parse(nativeSettings);
			if (!options || Array.isArray(options) || typeof options !== 'object')
				throw new Error('连接选项需要是 JSON 对象');
			const headers = customHeaders.trim() ? JSON.parse(customHeaders) : undefined;
			if (
				headers &&
				(Array.isArray(headers) ||
					typeof headers !== 'object' ||
					Object.values(headers).some((value) => typeof value !== 'string'))
			)
				throw new Error('请求头需要是字符串值的 JSON 对象');
			const created = !connectionEdit.id;
			const saved = await oiApi(
				'/connections' + (connectionEdit.id ? '/' + connectionEdit.id : ''),
				created ? 'POST' : 'PUT',
				{
					...connectionEdit,
					settings: {
						routing: connectionEdit.settings?.routing,
						...options,
						...templateFields,
						auth: connectionAuth
					},
					...(apiKey ? { api_key: apiKey } : {}),
					...(headers !== undefined ? { headers } : {})
				}
			);
			expanded = new Set([...expanded, saved.id]);
			connectionEdit = null;
			apiKey = '';
			toast.success(created ? '连接已创建，可刷新或添加模型' : '连接已保存');
		});
	const discoverModels = (connection: any) =>
		run(async () => {
			const found =
				connection.transport === 'browser'
					? await discoverBrowserModels(connection)
					: await oiApi(`/connections/${connection.id}/discover`, 'POST');
			expanded = new Set([...expanded, connection.id]);
			toast.success(`已刷新 ${found.length} 个模型，新发现的模型需要手动启用`);
		});

	const editModel = (connection: any, binding?: any) => {
		modelEdit = binding
			? structuredClone(binding)
			: {
					connection_id: connection.id,
					name: '',
					upstream_id: '',
					protocol: connection.protocol,
					protocol_override: '',
					enabled: true,
					capabilities: {},
					pricing: { currency: 'USD', rates: {} },
					params: {}
				};
		modelEdit.protocol_override ??= '';
		originalCapabilities = structuredClone(modelEdit.capabilities || {});
		modelEdit.pricing ||= {};
		modelEdit.pricing.rates ||= {};
		reasoningMode = modelEdit.capabilities.reasoning?.budget ? 'budget' : 'levels';
		budgetMin = modelEdit.capabilities.reasoning?.budget?.min;
		budgetMax = modelEdit.capabilities.reasoning?.budget?.max;
		budgetStep = modelEdit.capabilities.reasoning?.budget?.step;
		budgetDefault = modelEdit.capabilities.reasoning?.budget?.default;
		levels = (modelEdit.capabilities.reasoning?.levels || []).join(', ');
		reasoningParameter = modelEdit.capabilities.reasoning?.parameter || '';
		reasoningDefault = modelEdit.capabilities.reasoning?.default || '';
		customParams = JSON.stringify(modelEdit.params || {}, null, 2);
	};
	const saveModel = () =>
		run(async () => {
			if (reasoningParameter || originalCapabilities.reasoning) {
				const reasoning = {
					...(modelEdit.capabilities.reasoning || {}),
					parameter: reasoningParameter
				};
				delete reasoning.levels;
				delete reasoning.default;
				delete reasoning.budget;
				if (reasoningMode === 'budget')
					reasoning.budget = {
						min: budgetMin,
						max: budgetMax,
						step: budgetStep,
						default: budgetDefault
					};
				else {
					reasoning.levels = levels
						.split(',')
						.map((s) => s.trim())
						.filter(Boolean);
					if (reasoningDefault) reasoning.default = reasoningDefault;
				}
				modelEdit.capabilities.reasoning = reasoningParameter ? reasoning : {};
			}

			const overrides = { ...(modelEdit.capability_overrides || {}) };
			for (const [key, value] of Object.entries(modelEdit.capabilities)) {
				if (JSON.stringify(value) !== JSON.stringify(originalCapabilities[key]))
					overrides[key] = value;
			}
			modelEdit.capability_overrides = overrides;
			modelEdit.pricing.rates = Object.fromEntries(
				Object.entries(modelEdit.pricing.rates).filter(
					([, value]) => value !== '' && value !== null
				)
			);
			modelEdit.pricing.currency = 'USD';
			modelEdit.pricing.source ||= 'manual';
			modelEdit.pricing.version = new Date().toISOString();
			await oiApi(
				'/model-bindings' + (modelEdit.id ? '/' + modelEdit.id : ''),
				modelEdit.id ? 'PUT' : 'POST',
				{
					...modelEdit,
					protocol_override: modelEdit.protocol_override || null,
					params: JSON.parse(customParams)
				}
			);
			modelEdit = null;
		});
	$: scopedConnections = connections.filter(
		(c) => (!personal || !c.shared) && (showRemoved ? c.deleted : !c.deleted)
	);
	$: visible = scopedConnections
		.map((connection) => ({
			...connection,
			totalModels: bindings.filter((b) => b.connection_id === connection.id).length,
			models: bindings
				.filter(
					(b) =>
						b.connection_id === connection.id &&
						`${connection.name} ${templateName(connection)} ${connection.base_url || ''} ${b.name} ${b.upstream_id}`
							.toLowerCase()
							.includes(search.toLowerCase())
				)
				.sort(
					(a, b) =>
						(ranks.get(a.id) ?? Number.MAX_SAFE_INTEGER) -
						(ranks.get(b.id) ?? Number.MAX_SAFE_INTEGER)
				)
		}))
		.filter(
			(c) =>
				c.models.length ||
				`${c.name} ${templateName(c)} ${c.base_url || ''}`
					.toLowerCase()
					.includes(search.toLowerCase())
		);

	const toggleConnection = (connection: any, enabled: boolean) =>
		run(() => oiApi(`/connections/${connection.id}`, 'PUT', { ...connection, enabled }), false);
	const toggleModel = (model: any, enabled: boolean) =>
		run(() => oiApi(`/model-bindings/${model.id}`, 'PUT', { ...model, enabled }), false);
	$: filteredTemplates = templates.filter((t) =>
		`${t.name} ${templateDescription(t)}`.toLowerCase().includes(templateSearch.toLowerCase())
	);
</script>

<AdminSettingSection first>
	<div class="mb-2 flex justify-end">
		<button
			type="button"
			class="text-xs text-gray-500"
			on:click={() => (showRemoved = !showRemoved)}
			>{showRemoved ? '返回有效连接' : '已移除连接'}</button
		>
	</div>
	<div class="flex flex-wrap items-center justify-between gap-3 pb-1">
		<p class="text-xs text-gray-500 dark:text-gray-400">连接下独立管理模型、能力与价格</p>
		<button
			type="button"
			class="focus-ring flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs text-gray-600 transition-colors hover:bg-black/5 dark:text-gray-300 dark:hover:bg-white/5"
			on:click={() => {
				chooseTemplate = true;
				templateSearch = '';
			}}><Plus className="size-4" />新增连接</button
		>
	</div>
	{#if scopedConnections.length > 0}<div class="relative">
			<div class="absolute inset-y-0 left-2.5 flex items-center text-gray-400">
				<Search className="size-4" />
			</div>
			<input
				aria-label="搜索连接或模型"
				bind:value={search}
				placeholder="搜索连接或模型"
				class={inputClass + ' pl-9'}
			/>
		</div>{/if}
	<div class="space-y-0">
		{#each visible as connection (connection.id)}
			<div class="py-0" data-connection-id={connection.id}>
				<div class="flex min-w-0 items-center gap-2">
					<button
						type="button"
						class="focus-ring flex min-w-0 flex-1 items-center gap-3 rounded-lg py-1.5 text-left hover:bg-black/[0.025] dark:hover:bg-white/[0.025]"
						aria-expanded={expanded.has(connection.id)}
						on:click={() => toggle(connection.id)}
					>
						<div
							class="flex size-8 shrink-0 items-center justify-center rounded-xl bg-gray-100/70 text-xs font-medium text-gray-500 dark:bg-white/5 dark:text-gray-400"
						>
							{templateName(connection).slice(0, 1)}
						</div>
						<div class="min-w-0 flex-1">
							<div class="flex min-w-0 items-center gap-2">
								<span
									class="min-w-0 line-clamp-2 break-words text-sm text-gray-800 dark:text-gray-200 sm:truncate"
									>{connection.name}</span
								><span class="shrink-0 text-[0.6875rem] text-gray-400 dark:text-gray-600"
									>{connection.totalModels} 模型</span
								>
							</div>
							<p class="mt-0.5 truncate text-[0.6875rem] text-gray-400 dark:text-gray-500">
								{connection.transport === 'browser' ? '浏览器直连' : '服务器'} · {protocolLabels[
									connection.protocol
								] || connection.protocol}{connection.base_url ? ` · ${connection.base_url}` : ''}
							</p>
						</div>
						<span
							class="shrink-0 text-gray-400 transition-transform {expanded.has(connection.id)
								? ''
								: '-rotate-90'}"><ChevronDown className="size-3.5" /></span
						>
					</button>
					{#if connection.deleted && connection.owner_id}
						<button
							type="button"
							class="text-xs text-blue-600"
							disabled={busy}
							on:click={() =>
								run(() =>
									oiApi(
										`/connections/${connection.id}/restore?revision=${connection.revision}`,
										'POST'
									)
								)}>恢复为停用状态</button
						>
					{:else if connection.owner_id}
						<Tooltip content="编辑连接"
							><button
								type="button"
								aria-label={`编辑连接 ${connection.name}`}
								class={iconClass}
								on:click={() => editConnection(connection)}><Cog6 className="size-4" /></button
							></Tooltip
						>
						<Switch
							state={connection.enabled}
							disabled={busy}
							ariaLabel={`启用连接 ${connection.name}`}
							on:change={(e) => toggleConnection(connection, e.detail)}
						/>
						<Tooltip content="移除连接（可恢复）"
							><button
								type="button"
								class={iconClass}
								disabled={busy}
								aria-label={`移除连接 ${connection.name}`}
								on:click={() =>
									run(() =>
										oiApi(`/connections/${connection.id}?revision=${connection.revision}`, 'DELETE')
									)}><XMark className="size-3.5" /></button
							></Tooltip
						>
					{/if}
				</div>
				{#if expanded.has(connection.id) || search.trim()}
					<div class="ml-11 mt-1 border-l border-gray-100 pl-4 dark:border-white/5">
						<div
							class="max-h-[12.5rem] sm:max-h-40 overflow-y-auto overscroll-contain"
							role="list"
							aria-label={`${connection.name} 的模型`}
							data-testid="connection-model-list"
						>
							{#each connection.models as model (model.id)}
								<div class="flex h-10 sm:h-8 items-center gap-3 pe-1" role="listitem">
									<div class="min-w-0 flex-1">
										<p class="truncate text-[0.8125rem] leading-4 text-gray-700 dark:text-gray-300">
											{model.name}
										</p>
										{#if model.name.trim() !== model.upstream_id.trim() || !model.available}
											<p
												class="truncate text-[0.6875rem] leading-3 text-gray-500 dark:text-gray-400"
											>
												{model.name.trim() !== model.upstream_id.trim()
													? model.upstream_id
													: ''}{!model.available
													? model.name.trim() !== model.upstream_id.trim()
														? ' · 上游不可用'
														: '上游不可用'
													: ''}
											</p>
										{/if}
									</div>
									{#if connection.owner_id}<Tooltip content="配置模型"
											><button
												type="button"
												aria-label={`配置模型 ${model.name}`}
												class={iconClass}
												on:click={() => editModel(connection, model)}
												><Cog6 className="size-3.5" /></button
											></Tooltip
										><Switch
											state={model.enabled}
											disabled={busy}
											ariaLabel={`启用模型 ${model.name}`}
											on:change={(e) => toggleModel(model, e.detail)}
										/>{/if}
								</div>
							{/each}
						</div>
						{#if !connection.models.length}<p class="py-3 text-xs text-gray-400 dark:text-gray-500">
								{search
									? '没有匹配的模型。'
									: manualModels(connection)
										? '使用部署名称添加模型。'
										: '尚无模型，可刷新列表或手动添加。'}
							</p>{/if}
						{#if connection.owner_id}<div class="my-1 flex flex-wrap items-center gap-x-4 gap-y-1">
								{#if !manualModels(connection)}<button
										type="button"
										disabled={busy}
										class="focus-ring flex items-center gap-1.5 rounded-lg py-1.5 text-xs text-gray-500 hover:text-gray-800 disabled:opacity-40 dark:hover:text-gray-200"
										aria-label={`刷新 ${connection.name} 的模型`}
										on:click={() => discoverModels(connection)}
										><ArrowPath className="size-3.5" />刷新模型</button
									>{/if}
								<button
									type="button"
									disabled={busy}
									class="focus-ring flex items-center gap-1.5 rounded-lg py-1.5 text-xs text-gray-500 hover:text-gray-800 disabled:opacity-40 dark:hover:text-gray-200"
									on:click={() => editModel(connection)}
									><Plus className="size-3.5" />添加模型</button
								>
							</div>{/if}
					</div>
				{/if}
			</div>
		{/each}
	</div>
	{#if loading}<p class="py-6 text-center text-xs text-gray-400">正在加载连接…</p>
	{:else if loadError}<button
			type="button"
			class="py-6 text-xs text-gray-500"
			on:click={() => load()}>连接加载失败，点击重试</button
		>
	{:else if !scopedConnections.length}<p
			class="py-6 text-center text-xs leading-relaxed text-gray-400 dark:text-gray-500"
		>
			选择一个连接模板开始。<br />同一模板可创建多个独立连接。
		</p>{/if}
	{#if !loading && !loadError && !visible.length && scopedConnections.length}<p
			class="py-6 text-center text-xs text-gray-400"
		>
			没有找到匹配的连接或模型
		</p>{/if}
	{#if personal || $user?.role === 'admin'}<div class="pt-2">
			<button
				type="button"
				class="text-[0.6875rem] text-gray-400 transition-colors hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
				disabled={busy}
				on:click={() => (showMigration = true)}
				>{personal ? '迁移旧浏览器直连' : '预览并迁移原版配置'}</button
			>
		</div>{/if}
</AdminSettingSection>

{#if showMigration}<ConnectionMigrationModal
		scope={personal ? 'personal' : 'server'}
		onClose={() => (showMigration = false)}
		onApplied={async () => {
			await load();
			await models.set(await getModels());
		}}
	/>{/if}

{#if chooseTemplate}
	<Modal show size="md" ariaLabel="选择连接模板" onRequestClose={() => (chooseTemplate = false)}>
		<div class="px-5 pb-5 pt-4">
			<div class="mb-4 flex items-center justify-between">
				<h2 class="text-lg font-medium">新增连接</h2>
				<button
					type="button"
					aria-label="关闭模板选择"
					class={iconClass}
					on:click={() => (chooseTemplate = false)}><XMark className="size-5" /></button
				>
			</div>
			<p class="mb-4 text-xs text-gray-500">选择模板，配置接口地址和凭据，再添加模型。</p>
			<input
				aria-label="搜索连接模板"
				class={inputClass}
				placeholder="搜索连接模板"
				bind:value={templateSearch}
			/>
			<div class="mt-3 grid max-h-[56dvh] grid-cols-1 gap-1 overflow-y-auto sm:grid-cols-2">
				{#each filteredTemplates as template}<button
						type="button"
						class="focus-ring flex items-center gap-3 rounded-xl px-3 py-3 text-left transition-colors hover:bg-gray-50 dark:hover:bg-white/[0.04]"
						on:click={() => create(template)}
						><span
							class="flex size-9 shrink-0 items-center justify-center rounded-xl bg-gray-100/70 text-sm text-gray-500 dark:bg-white/5 dark:text-gray-400"
							>{template.name.slice(0, 1)}</span
						><span class="min-w-0"
							><span class="block text-sm text-gray-800 dark:text-gray-200">{template.name}</span
							><span class="mt-0.5 block text-[0.6875rem] text-gray-400 dark:text-gray-500"
								>{templateDescription(template)}</span
							></span
						></button
					>{/each}
			</div>
		</div>
	</Modal>
{/if}
{#if connectionEdit}
	<Modal show size="sm" ariaLabel="编辑连接" onRequestClose={() => (connectionEdit = null)}>
		<div class="flex items-center justify-between px-5 pb-2 pt-4">
			<h2 class="text-lg font-medium">{connectionEdit.id ? '编辑连接' : '新增连接'}</h2>
			<button
				type="button"
				aria-label="关闭连接编辑"
				class={iconClass}
				on:click={() => (connectionEdit = null)}><XMark className="size-5" /></button
			>
		</div>
		<form
			class="max-h-[78dvh] space-y-4 overflow-y-auto px-5 pb-5"
			on:submit|preventDefault={saveConnection}
		>
			<div class="flex items-center justify-between gap-3 text-xs text-gray-400 dark:text-gray-500">
				<span>{templateName(connectionEdit)}</span>
				{#if !connectionEdit.id}<button
						type="button"
						class="hover:text-gray-700 dark:hover:text-gray-200"
						disabled={busy}
						on:click={() => {
							connectionEdit = null;
							chooseTemplate = true;
						}}>更换模板</button
					>{/if}
			</div>
			<label class="block space-y-1.5 text-xs text-gray-500"
				>连接名称<input required bind:value={connectionEdit.name} class={inputClass} /></label
			>
			<label class="block space-y-1.5 text-xs text-gray-500"
				>请求执行位置
				<SettingsSelect
					className="w-full"
					ariaLabel="请求执行位置"
					bind:value={connectionEdit.transport}
					disabled={!!connectionEdit.id}
					on:change={() => {
						if (connectionEdit.transport === 'browser') connectionEdit.shared = false;
					}}
				>
					<option value="server">服务器</option><option
						value="browser"
						disabled={!protocolDirectory.find((p) => p.id === connectionEdit.protocol)
							?.browser_supported}>浏览器直连</option
					>
				</SettingsSelect>
			</label>
			{#if connectionEdit.id && connectionEdit.transport === 'browser'}<button
					type="button"
					class="text-xs text-blue-600"
					disabled={busy}
					on:click={() =>
						run(async () => {
							const copied = await oiApi(`/connections/${connectionEdit.id}/server-copy`, 'POST', {
								revision: connectionEdit.revision,
								request_id: crypto.randomUUID()
							});
							expanded = new Set([...expanded, copied.connection_id]);
							connectionEdit = null;
							toast.success('已验证服务器可达并创建个人连接，原直连保留');
						})}>验证并复制为服务器个人连接</button
				>{/if}
			<label class="block space-y-1.5 text-xs text-gray-500"
				>服务地址<input
					required
					type="url"
					bind:value={connectionEdit.base_url}
					class={inputClass}
					placeholder="https://example.com"
					aria-describedby="service-address-hint"
				/></label
			>
			<p id="service-address-hint" class="text-xs text-gray-500">
				填写服务根地址，接口模式会自动生成 /v1、/v1beta 等路径。
			</p>
			<label class="block space-y-1.5 text-xs text-gray-500"
				>接口模式
				<SettingsSelect
					bind:value={connectionEdit.protocol}
					className="w-full"
					ariaLabel="连接接口模式"
				>
					<optgroup label="常用协议"
						>{#each protocolDirectory.filter((p) => !p.extension) as protocol}<option
								value={protocol.id}>{protocol.label}</option
							>{/each}</optgroup
					>
					<optgroup label="扩展协议"
						>{#each protocolDirectory.filter((p) => p.extension) as protocol}<option
								value={protocol.id}
								disabled={connectionEdit.transport === 'browser' && !protocol.browser_supported}
								>{protocol.label}</option
							>{/each}</optgroup
					>
				</SettingsSelect>
			</label>
			<ConnectionEndpointPreview
				connection={connectionEdit}
				protocol={connectionEdit.protocol}
				settingsText={nativeSettings}
				extraSettings={{ ...templateFields, auth: connectionAuth }}
			/>
			{#if connectionEdit.settings?.routing?.needs_review}<button
					type="button"
					class="text-xs text-blue-600 dark:text-blue-400"
					on:click={() => {
						connectionEdit.settings.routing = {
							version: 1,
							profile: connectionEdit.settings.routing.profile,
							prefixes: {}
						};
						connectionEdit = { ...connectionEdit };
					}}>改用自动路径规则并预览</button
				>{/if}
			<p class="text-xs text-gray-500">
				接口模式决定请求格式，实际支持范围以服务商为准。请求失败不会自动切换模式。
			</p>
			{#each templateFieldsFor(connectionEdit) as field}
				<label class="block space-y-1.5 text-xs text-gray-500"
					>{field.label}
					<input
						required={field.required}
						placeholder={field.placeholder}
						bind:value={templateFields[field.key]}
						class={inputClass}
					/>
				</label>
			{/each}
			{#if connectionAuth !== 'none'}<div class="space-y-1.5 text-xs text-gray-500">
					<span>API Key</span><SensitiveInput
						type="password"
						required={false}
						autocomplete="new-password"
						bind:value={apiKey}
						placeholder={connectionEdit.has_credentials ? '留空保留原凭据' : '输入 API Key'}
						outerClassName={inputClass + ' flex'}
					/>
				</div>
			{/if}
			<div class="flex items-center justify-between gap-4 text-xs text-gray-500">
				<span>启用连接</span><Switch bind:state={connectionEdit.enabled} ariaLabel="启用连接" />
			</div>
			{#if $user?.role === 'admin' && connectionEdit.transport !== 'browser'}<div
					class="flex items-center justify-between gap-4 text-xs text-gray-500"
				>
					<span>对其他用户共享</span><Switch
						bind:state={connectionEdit.shared}
						ariaLabel="共享连接"
					/>
				</div>{/if}
			<details class="text-xs text-gray-500">
				<summary class="cursor-pointer">高级选项</summary>
				<div class="mt-3 space-y-3">
					<label class="block space-y-1.5"
						>认证方式<SettingsSelect bind:value={connectionAuth} className="w-full">
							<option value="bearer">Bearer Token</option><option value="x-api-key"
								>x-api-key</option
							>
							<option value="x-goog-api-key">x-goog-api-key</option><option value="api-key"
								>api-key</option
							><option value="none">无需认证</option>
						</SettingsSelect></label
					><label class="block space-y-1.5"
						>自定义请求头<textarea
							aria-label="自定义请求头 JSON"
							class={inputClass + ' font-mono text-xs'}
							rows="3"
							bind:value={customHeaders}
							placeholder={'留空保留现有请求头；{} 清空'}
							autocomplete="off"
						></textarea><span class="text-xs text-gray-500"
							>加密保存；不会在普通连接列表中回显。</span
						></label
					><label class="block space-y-1.5"
						>连接选项<textarea
							aria-label="连接选项 JSON"
							class={inputClass + ' font-mono text-xs'}
							rows="5"
							bind:value={nativeSettings}
						></textarea></label
					>
				</div>
			</details>
			<div class="flex justify-end gap-2 pt-1">
				<button
					type="button"
					class="rounded-lg px-3 py-1.5 text-xs text-gray-500 hover:bg-black/5 dark:hover:bg-white/5"
					on:click={() => (connectionEdit = null)}>取消</button
				><button type="submit" disabled={busy} class={primaryClass}>保存</button>
			</div>
		</form>
	</Modal>
{/if}
{#if modelEdit}
	<Modal show size="md" ariaLabel="配置模型" onRequestClose={() => (modelEdit = null)}>
		<div class="flex items-center justify-between px-5 pb-2 pt-4">
			<h2 class="text-lg font-medium">配置模型</h2>
			<button
				type="button"
				aria-label="关闭模型配置"
				class={iconClass}
				on:click={() => (modelEdit = null)}><XMark className="size-5" /></button
			>
		</div>
		<form
			class="max-h-[78dvh] space-y-4 overflow-y-auto px-5 pb-5"
			on:submit|preventDefault={saveModel}
		>
			<p class="text-xs text-gray-400 dark:text-gray-500">
				{connections.find((c) => c.id === modelEdit.connection_id)?.name}
			</p>
			<div class="grid gap-4 sm:grid-cols-2">
				<label class="space-y-1.5 text-xs text-gray-500"
					>显示名称<input class={inputClass} required bind:value={modelEdit.name} /></label
				><label class="space-y-1.5 text-xs text-gray-500"
					>{connections.find((c) => c.id === modelEdit.connection_id)?.template === 'azure-openai'
						? '部署名称'
						: '上游模型 ID'}<input
						class={inputClass}
						required
						bind:value={modelEdit.upstream_id}
					/></label
				>
			</div>
			<div class="grid gap-4 sm:grid-cols-2">
				<label class="space-y-1.5 text-xs text-gray-500"
					>输入窗口<input
						class={inputClass}
						type="number"
						min="1"
						bind:value={modelEdit.capabilities.input_limit}
						placeholder="未知"
					/></label
				><label class="space-y-1.5 text-xs text-gray-500"
					>最大输出<input
						class={inputClass}
						type="number"
						min="1"
						bind:value={modelEdit.capabilities.output_limit}
						placeholder="未知"
					/></label
				>
			</div>
			<details class="text-xs text-gray-500">
				<summary class="cursor-pointer">模型能力</summary>
				<div class="mt-3 grid grid-cols-2 gap-x-6 gap-y-3">
					{#each capabilities as capability}<div class="flex items-center justify-between gap-4">
							<span>{capability}</span><Switch
								state={modelEdit.capabilities[capability] === true}
								ariaLabel={capability}
								on:change={(e) => (modelEdit.capabilities[capability] = e.detail)}
							/>
						</div>{/each}
				</div>
				<p class="mt-3 text-[0.6875rem] text-gray-400">仅开启已确认支持的能力。</p>
			</details>
			<details class="text-xs text-gray-500">
				<summary class="cursor-pointer">推理控件</summary>
				<div class="mt-3 space-y-3">
					<label class="block space-y-1.5"
						>控件类型<SettingsSelect className="w-full" bind:value={reasoningMode}>
							<option value="levels">推理强度档位</option><option value="budget"
								>推理 token 预算</option
							>
						</SettingsSelect></label
					>
					<label class="block space-y-1.5"
						>参数路径<input
							class={inputClass}
							bind:value={reasoningParameter}
							required={Boolean(
								levels.trim() ||
								budgetMin !== undefined ||
								budgetMax !== undefined ||
								budgetStep !== undefined
							)}
							placeholder={reasoningMode === 'budget'
								? 'thinking.budget_tokens'
								: 'reasoning.effort'}
						/></label
					>
					{#if reasoningMode === 'budget'}
						<label class="block space-y-1.5 text-xs text-gray-500"
							>接口模式
							<SettingsSelect
								className="w-full"
								bind:value={modelEdit.protocol_override}
								ariaLabel="模型接口模式"
							>
								<option value=""
									>继承连接 · {protocolLabels[modelConnection?.protocol] ||
										modelConnection?.protocol}</option
								>
								<optgroup label="常用协议"
									>{#each protocolDirectory.filter((p) => !p.extension) as protocol}<option
											value={protocol.id}>{protocol.label}</option
										>{/each}</optgroup
								>
								<optgroup label="扩展协议"
									>{#each protocolDirectory.filter((p) => p.extension) as protocol}<option
											value={protocol.id}
											disabled={modelConnection?.transport === 'browser' &&
												!protocol.browser_supported}>{protocol.label}</option
										>{/each}</optgroup
								>
							</SettingsSelect>
						</label>
						<p class="text-xs text-gray-500">
							实际模式：{protocolLabels[modelEdit.protocol_override || modelConnection?.protocol]} ·
							{modelEdit.protocol_override ? '模型单独设置' : '跟随连接'}
						</p>
						<ConnectionEndpointPreview
							connection={modelConnection}
							protocol={modelEdit.protocol_override || modelConnection?.protocol}
							model={modelEdit.upstream_id}
						/>
						<div class="grid grid-cols-2 gap-3">
							<label class="space-y-1.5"
								>最小预算<input
									class={inputClass}
									type="number"
									step="1"
									bind:value={budgetMin}
									required={Boolean(reasoningParameter)}
									placeholder="未知"
								/></label
							>
							<label class="space-y-1.5"
								>最大预算<input
									class={inputClass}
									type="number"
									step="1"
									bind:value={budgetMax}
									required={Boolean(reasoningParameter)}
									placeholder="未知"
								/></label
							>
							<label class="space-y-1.5"
								>步长<input
									class={inputClass}
									type="number"
									min="1"
									step="1"
									bind:value={budgetStep}
									required={Boolean(reasoningParameter)}
									placeholder="未知"
								/></label
							>
							<label class="space-y-1.5"
								>默认预算<input
									class={inputClass}
									type="number"
									step="1"
									bind:value={budgetDefault}
									placeholder="沿用上游默认"
								/></label
							>
						</div>
					{:else}
						<label class="block space-y-1.5"
							>档位（按滑杆顺序，逗号分隔）<input
								class={inputClass}
								bind:value={levels}
								placeholder="low, medium, high"
							/></label
						>
						<label class="block space-y-1.5"
							>默认档位<input class={inputClass} bind:value={reasoningDefault} /></label
						>
					{/if}
					<p class="text-[0.6875rem] text-gray-400">
						仅填写已确认的模型能力，保存后自动生成原生控件。
					</p>
				</div>
			</details>
			<details class="text-xs text-gray-500">
				<summary class="cursor-pointer">计价 · USD / 百万 token</summary>
				<div class="mt-3 grid gap-3 sm:grid-cols-2">
					{#each rates as rate}<label class="space-y-1.5"
							>{rate}<input
								class={inputClass}
								inputmode="decimal"
								bind:value={modelEdit.pricing.rates[rate]}
								placeholder="未知"
							/></label
						>{/each}
				</div>
			</details>
			<details class="text-xs text-gray-500">
				<summary class="cursor-pointer">高级参数</summary>
				<div class="mt-3 space-y-3">
					<textarea
						aria-label="模型参数 JSON"
						rows="7"
						class={inputClass + ' font-mono text-xs'}
						bind:value={customParams}
					></textarea>
				</div>
			</details>
			<div class="flex justify-end gap-2 pt-1">
				<button
					type="button"
					class="rounded-lg px-3 py-1.5 text-xs text-gray-500 hover:bg-black/5 dark:hover:bg-white/5"
					on:click={() => (modelEdit = null)}>取消</button
				><button type="submit" disabled={busy} class={primaryClass}>保存</button>
			</div>
		</form>
	</Modal>
{/if}
