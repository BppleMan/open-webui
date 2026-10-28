<script lang="ts">
	import { canManageChats } from '$lib/utils/settings-access';
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { user, showSettings } from '$lib/stores';
	import ArchiveImportModal from '../ArchiveImportModal.svelte';
	import JsonImportModal from '../JsonImportModal.svelte';
	import { refreshSidebar } from '$lib/stores/chatList';

	import { archiveAllChats, deleteAllChats, getAllChats, importChats } from '$lib/apis/chats';
	import { getImportOrigin, convertOpenAIChats } from '$lib/utils';
	import { getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import SharedChatsModal from '$lib/components/layout/SharedChatsModal.svelte';
	import FilesModal from '$lib/components/layout/FilesModal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import UserSettingRow from './UserSettingRow.svelte';
	import UserSettingSection from './UserSettingSection.svelte';

	const i18n = getContext('i18n');

	export let saveSettings: Function;

	// Chats
	let importFiles;

	let showArchiveConfirmDialog = false;
	let showDeleteConfirmDialog = false;
	let showSharedChatsModal = false;
	let showFilesModal = false;

	let chatImportInputElement: HTMLInputElement;
	let archiveInput: HTMLInputElement;
	let archiveFile: File | null = null;
	let showArchiveImport = false;
	const actionButtonClass =
		'text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white';

	let pendingImports: any[] = [],
		showJsonImport = false,
		jsonImportId = '';
	$: if (importFiles?.length) {
		const selected = importFiles[0];
		importFiles = null;
		void inspectJson(selected);
	}
	async function inspectJson(file: File) {
		try {
			if (file.size > 64 * 1024 * 1024) throw new Error('JSON 文件超过 64 MiB');
			const text = await file.text();
			let _chats = JSON.parse(text);
			if (!Array.isArray(_chats)) throw new Error('请选择会话 JSON 数组');
			if (getImportOrigin(_chats) === 'openai') _chats = convertOpenAIChats(_chats);
			pendingImports = _chats.map((chat) => {
				if (chat.chat) {
					return {
						chat: chat.chat,
						meta: chat.meta ?? {},
						variables: chat?.variables ?? {},
						pinned: chat?.pinned ?? false,
						archived: chat?.archived ?? false,
						folder_id: chat?.folder_id ?? null,
						created_at: chat?.created_at ?? null,
						updated_at: chat?.updated_at ?? null
					};
				} else {
					// Legacy format
					return {
						chat: chat,
						meta: {},
						pinned: false,
						folder_id: null,
						created_at: chat?.created_at ?? null,
						updated_at: chat?.updated_at ?? null
					};
				}
			});
			jsonImportId =
				'json:' +
				Array.from(
					new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))
				)
					.map((x) => x.toString(16).padStart(2, '0'))
					.join('');
			showJsonImport = true;
		} catch (error) {
			toast.error(String(error));
		} finally {
			if (chatImportInputElement) chatImportInputElement.value = '';
		}
	}

	const exportChats = async () => {
		let blob = new Blob([JSON.stringify(await getAllChats(localStorage.token))], {
			type: 'application/json'
		});
		saveAs(blob, `chat-export-${Date.now()}.json`);
	};

	const archiveAllChatsHandler = async () => {
		await goto('/');
		const success = await archiveAllChats(localStorage.token).catch((error) => {
			toast.error(`${error}`);
		});
		if (!success) return;

		await refreshSidebar(localStorage.token);
	};

	const deleteAllChatsHandler = async () => {
		await goto('/');
		const success = await deleteAllChats(localStorage.token).catch((error) => {
			toast.error(`${error}`);
		});
		if (!success) return;

		await refreshSidebar(localStorage.token);
	};
</script>

<JsonImportModal
	bind:show={showJsonImport}
	forms={pendingImports}
	importId={jsonImportId}
	onImported={async (result) => {
		await refreshSidebar(localStorage.token);
		toast.success(`已导入 ${result.length} 个会话`);
		if (result.length === 1) {
			showSettings.set(false);
			await goto(`/c/${result[0].id}`);
		}
	}}
/>
<SharedChatsModal bind:show={showSharedChatsModal} />
<FilesModal bind:show={showFilesModal} />
<ArchiveImportModal
	bind:show={showArchiveImport}
	file={archiveFile}
	onImported={async (result) => {
		await refreshSidebar(localStorage.token);
		showSettings.set(false);
		await goto(`/c/${result.id}`);
		toast.success('会话归档已还原');
	}}
/>

<ConfirmDialog
	title={$i18n.t('settings.personal.dataControls.archiveAllChats.label')}
	message={$i18n.t('Are you sure you want to archive all chats? This action cannot be undone.')}
	bind:show={showArchiveConfirmDialog}
	on:confirm={archiveAllChatsHandler}
	on:cancel={() => {
		showArchiveConfirmDialog = false;
	}}
/>

<ConfirmDialog
	title={$i18n.t('settings.personal.dataControls.deleteAllChats.label')}
	message={$i18n.t('Are you sure you want to delete all chats? This action cannot be undone.')}
	bind:show={showDeleteConfirmDialog}
	on:confirm={deleteAllChatsHandler}
	on:cancel={() => {
		showDeleteConfirmDialog = false;
	}}
/>

<div id="tab-chats" class="flex flex-col h-full text-sm">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.personal.dataControls.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<input
			id="chat-import-input"
			bind:this={chatImportInputElement}
			bind:files={importFiles}
			type="file"
			accept=".json"
			hidden
		/>

		<UserSettingSection
			title={$i18n.t('settings.personal.dataControls.sections.chats.title')}
			first
		>
			{#if canManageChats({ user: $user, config: null }, 'import')}
				<input
					type="file"
					accept=".zip"
					bind:this={archiveInput}
					hidden
					on:change={(event) => {
						archiveFile = event.currentTarget.files?.[0] ?? null;
						if (archiveFile) showArchiveImport = true;
						event.currentTarget.value = '';
					}}
				/>
				<UserSettingRow
					label="还原 OI 归档"
					description="还原包含费用、分支、修订和附件的会话 ZIP 归档。"
					><button class={actionButtonClass} type="button" on:click={() => archiveInput.click()}
						>选择归档</button
					></UserSettingRow
				>
				<UserSettingRow
					label={$i18n.t('settings.personal.dataControls.importChats.label')}
					description={$i18n.t('settings.personal.dataControls.importChats.description')}
				>
					<button
						class={actionButtonClass}
						on:click={() => {
							chatImportInputElement.click();
						}}
						type="button"
					>
						{$i18n.t('Import')}
					</button>
				</UserSettingRow>
			{/if}

			{#if canManageChats({ user: $user, config: null }, 'export')}
				<UserSettingRow
					label={$i18n.t('settings.personal.dataControls.exportChats.label')}
					description={$i18n.t('settings.personal.dataControls.exportChats.description')}
				>
					<button
						class={actionButtonClass}
						on:click={() => {
							exportChats();
						}}
						type="button"
					>
						{$i18n.t('Export')}
					</button>
				</UserSettingRow>
			{/if}

			<UserSettingRow
				label={$i18n.t('settings.personal.dataControls.sharedChats.label')}
				description={$i18n.t('settings.personal.dataControls.sharedChats.description')}
			>
				<button
					class={actionButtonClass}
					on:click={() => {
						showSharedChatsModal = true;
					}}
					type="button"
				>
					{$i18n.t('Manage')}
				</button>
			</UserSettingRow>

			<UserSettingRow
				label={$i18n.t('settings.personal.dataControls.archiveAllChats.label')}
				description={$i18n.t('settings.personal.dataControls.archiveAllChats.description')}
			>
				<button
					class={actionButtonClass}
					on:click={() => {
						showArchiveConfirmDialog = true;
					}}
					type="button"
				>
					{$i18n.t('settings.personal.dataControls.archiveAll.label')}
				</button>
			</UserSettingRow>

			{#if canManageChats({ user: $user, config: null }, 'delete')}
				<UserSettingRow
					label={$i18n.t('settings.personal.dataControls.deleteAllChats.label')}
					description={$i18n.t('settings.personal.dataControls.deleteAllChats.description')}
				>
					<button
						class={actionButtonClass}
						on:click={() => {
							showDeleteConfirmDialog = true;
						}}
						type="button"
					>
						{$i18n.t('Delete All')}
					</button>
				</UserSettingRow>
			{/if}
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.dataControls.sections.files.title')}>
			<UserSettingRow
				label={$i18n.t('settings.personal.dataControls.manageFiles.label')}
				description={$i18n.t('settings.personal.dataControls.manageFiles.description')}
			>
				<button
					class={actionButtonClass}
					on:click={() => {
						showFilesModal = true;
					}}
					type="button"
				>
					{$i18n.t('Manage')}
				</button>
			</UserSettingRow>
		</UserSettingSection>
	</div>
</div>
