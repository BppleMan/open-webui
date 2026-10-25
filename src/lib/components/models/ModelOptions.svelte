<script lang="ts">
	import { groupModels, capableModels } from '$lib/utils/modelDirectory';
	export let items: any[] = [];
	export let capability: string | undefined = undefined;
	$: groups = groupModels(capableModels(items, capability));
</script>

{#each groups as group (group.connection.id)}
	<optgroup label={group.connection.name}>
		{#each group.items as model (model.id)}
			<option value={model.id}>{model.name || model.id}</option>
		{/each}
	</optgroup>
{/each}
