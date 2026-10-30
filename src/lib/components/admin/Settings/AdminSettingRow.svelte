<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';

	export let label = '';
	export let description = '';
	export let className = '';
	export let labelClassName = '';

	const labelId = `admin-setting-${uuidv4()}`;
</script>

<div
	class="group/setting-row grid max-w-[48rem] grid-cols-[minmax(0,1fr)_auto] items-center gap-x-6 {className}"
>
	<div class="min-w-0">
		<label
			for={`${labelId}-control`}
			id={labelId}
			class="text-xs leading-5 text-gray-700 dark:text-gray-300 {labelClassName}"
		>
			<slot name="label">{label}</slot>
		</label>
		{#if description}<p class="mt-1 text-[0.6875rem] leading-4 text-gray-500 dark:text-gray-400">
				{description}
			</p>{/if}
	</div>
	<div class="min-w-0 justify-self-end"><slot {labelId} /></div>
</div>
