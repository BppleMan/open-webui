<script lang="ts">
	import { createEventDispatcher, onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getModels, getBackendConfig } from '$lib/apis';
	import { getConnectionsConfig, setConnectionsConfig } from '$lib/apis/configs';
	import { config, models, settings } from '$lib/stores';
	import ConnectionManager from '$lib/components/models/ConnectionManager.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ArrowPath from '$lib/components/icons/ArrowPath.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');
	const dispatch = createEventDispatcher();
	let connectionsConfig: any = null;
	let modelListRefreshing = false;
	let loadError = false;
	const load = async () => {
		loadError = false;
		try {
			connectionsConfig = await getConnectionsConfig(localStorage.token);
		} catch (error) {
			loadError = true;
			toast.error(String(error));
		}
	};
	onMount(load);
	const refreshModelListHandler = async () => {
		modelListRefreshing = true;
		try {
			models.set(
				await getModels(
					localStorage.token,
					$config?.features?.enable_direct_connections
						? ($settings?.directConnections ?? null)
						: null,
					false,
					true
				)
			);
		} catch (error) {
			toast.error(String(error));
		} finally {
			modelListRefreshing = false;
		}
	};
	const updateConnectionsHandler = async () => {
		try {
			await setConnectionsConfig(localStorage.token, connectionsConfig);
			config.set(await getBackendConfig());
		} catch (error) {
			toast.error(String(error));
		}
	};
	const submitHandler = () => dispatch('save');
</script>

<div class="flex h-full flex-col justify-between text-sm">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.admin.connections.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if connectionsConfig !== null}
			<ConnectionManager />

			<AdminSettingSection
				title={$i18n.t('settings.admin.connections.sections.userConnections.title')}
			>
				<AdminSettingRow
					label={$i18n.t('settings.admin.connections.directConnections.label')}
					description={$i18n.t('settings.admin.connections.directConnections.description')}
					let:labelId
				>
					<Switch
						bind:state={connectionsConfig.ENABLE_DIRECT_CONNECTIONS}
						on:change={async () => {
							updateConnectionsHandler();
						}}
						ariaLabelledbyId={labelId}
					/>
				</AdminSettingRow>

				<AdminSettingRow
					label={$i18n.t('settings.admin.connections.cacheBaseModelList.label')}
					description={$i18n.t('settings.admin.connections.cacheBaseModelList.description')}
					let:labelId
				>
					<div class="flex items-center gap-1.5">
						{#if connectionsConfig.ENABLE_BASE_MODELS_CACHE}
							<Tooltip content={$i18n.t('settings.admin.connections.refreshModels.label')}>
								<button
									class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-black/5 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-600 dark:hover:bg-white/5 dark:hover:text-gray-300"
									type="button"
									disabled={modelListRefreshing}
									on:click={refreshModelListHandler}
									aria-label={$i18n.t('settings.admin.connections.refreshModels.label')}
								>
									{#if modelListRefreshing}
										<Spinner className="size-3.5" />
									{:else}
										<ArrowPath className="size-4" />
									{/if}
								</button>
							</Tooltip>
						{/if}

						<Switch
							bind:state={connectionsConfig.ENABLE_BASE_MODELS_CACHE}
							on:change={async () => {
								updateConnectionsHandler();
							}}
							ariaLabelledbyId={labelId}
						/>
					</div>
				</AdminSettingRow>
			</AdminSettingSection>
		{:else if loadError}
			<button class="text-xs text-gray-500" on:click={load}>加载失败，重试</button>
		{:else}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-6 text-sm font-normal">
		<button
			class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			type="button"
			on:click={submitHandler}
		>
			{$i18n.t('Done')}
		</button>
	</div>
</div>
