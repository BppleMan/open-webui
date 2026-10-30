<script>
	import { sortByConnection, modelConnection } from '$lib/utils/modelDirectory';
	import Sortable from 'sortablejs';

	import { onMount, tick } from 'svelte';

	import {
		chatId,
		mobile,
		chatModels as models,
		pinnedModels,
		settings,
		showSidebar,
		visiblePinnedModels
	} from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';
	import PinnedModelItem from './PinnedModelItem.svelte';

	export let selectedChatId = null;
	export let shiftKey = false;
	/** @type {any[]} */
	let pinnedItems = [];
	$: pinnedItems = sortByConnection(
		$visiblePinnedModels.map((id) => $models.find((model) => model.id === id)).filter(Boolean)
	);

	const initPinnedModelsSortable = () => {
		const pinnedModelsList = document.getElementById('pinned-models-list');
		if (pinnedModelsList && !$mobile) {
			new Sortable(pinnedModelsList, {
				animation: 150,
				draggable: '[data-id]',
				onMove: (/** @type {{dragged:HTMLElement,related:HTMLElement}} */ event) =>
					modelConnection(event.dragged.dataset.id).id ===
					modelConnection(event.related.dataset.id).id,
				setData: function (dataTransfer, dragEl) {
					dataTransfer.setData(
						'text/plain',
						JSON.stringify({
							type: 'model',
							id: dragEl.dataset.id
						})
					);
				},
				onUpdate: async (event) => {
					const reorderedIds = pinnedItems.map((model) => model.id);
					const [movedId] = reorderedIds.splice(event.oldDraggableIndex, 1);
					reorderedIds.splice(event.newDraggableIndex, 0, movedId);

					// Keep pins for models the user cannot see
					settings.set({
						...$settings,
						pinnedModels: [
							...reorderedIds,
							...$pinnedModels.filter((id) => !$visiblePinnedModels.includes(id))
						]
					});
					await updateUserSettings(localStorage.token, {
						ui: { pinnedModels: $settings.pinnedModels }
					});
				}
			});
		}
	};

	onMount(async () => {
		await tick();
		initPinnedModelsSortable();
	});
</script>

<div class="mt-0.5 pb-1.5" id="pinned-models-list" role="list">
	{#each pinnedItems as model, modelIdx (model.id)}
		{@const modelId = model.id}
		{#if modelIdx === 0 || modelConnection(pinnedItems[modelIdx - 1]).id !== modelConnection(model).id}
			<div
				class="truncate px-2 pt-2 pb-1 text-[0.6875rem] text-gray-500"
				title={modelConnection(model).name}
			>
				{modelConnection(model).name}
			</div>
		{/if}
		<PinnedModelItem
			{model}
			{shiftKey}
			onClick={() => {
				selectedChatId = null;
				chatId.set('');
				if ($mobile) {
					showSidebar.set(false);
				}
			}}
			onUnpin={() => {
				settings.set({
					...$settings,
					pinnedModels: $pinnedModels.filter((id) => id !== modelId)
				});
				updateUserSettings(localStorage.token, { ui: { pinnedModels: $settings.pinnedModels } });
			}}
		/>
	{/each}
</div>
