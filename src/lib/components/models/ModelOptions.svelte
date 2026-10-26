<script lang="ts">
	import { groupModels, capableModels } from '$lib/utils/modelDirectory';
	export let items: any[] = [];
	export let capability: string | undefined = undefined;
	export let serverOnly = false;
	$: groups = groupModels(capableModels(items.filter(model=>!serverOnly || !model.direct), capability));
</script>

{#each groups as group (group.connection.id)}
	<optgroup
		label={group.connection.name +
			(group.connection.transport === 'browser' ? ' · 浏览器直连' : '')}
	>
		{#each group.items as model (model.id)}
			<option value={model.id}
				>{model.name || model.id} · {group.connection.name}{group.connection.transport === 'browser'
					? ' · 浏览器直连'
					: ''}</option
			>
		{/each}
	</optgroup>
{/each}
