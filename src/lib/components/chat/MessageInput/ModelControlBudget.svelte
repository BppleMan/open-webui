<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import type { ModelControl } from '$lib/apis';
	const i18n: any = getContext('i18n');
	const dispatch = createEventDispatcher<{ change: string }>();
	export let control: ModelControl;
	export let id: string;
	export let value = '';
	export let disabled = false;
	let current = 0;
	$: budget = control.budget!;
	$: current = value === '' ? (budget.default ?? budget.min) : Number(value);
	$: invalid =
		!Number.isInteger(current) ||
		current < budget.min ||
		current > budget.max ||
		(current - budget.min) % budget.step !== 0;
	$: progress = Math.max(
		0,
		Math.min(100, ((current - budget.min) / (budget.max - budget.min)) * 100)
	);
	const commit = () => {
		if (
			Number.isInteger(current) &&
			current >= budget.min &&
			current <= budget.max &&
			(current - budget.min) % budget.step === 0
		)
			dispatch('change', String(current));
	};
</script>

<div class="space-y-2 px-2 py-2 text-xs">
	<div class="flex items-center justify-between gap-3">
		<label for={id} class="text-gray-700 dark:text-gray-100">{control.label}</label>
		<span class="text-gray-500"
			>{value === '' && budget.default == null
				? $i18n.t('Default')
				: `${current.toLocaleString()} tokens`}</span
		>
	</div>
	{#if control.description}<p class="text-gray-500">{control.description}</p>{/if}
	<input
		type="range"
		{id}
		min={budget.min}
		max={budget.max}
		step={budget.step}
		bind:value={current}
		{disabled}
		aria-label={control.label}
		aria-valuetext={`${current} tokens`}
		class="control-slider h-5 w-full border-y border-transparent bg-clip-padding cursor-pointer appearance-none rounded-md bg-gray-100 outline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-gray-400 disabled:cursor-wait [--slider-fill:var(--color-gray-500)] dark:bg-gray-800 dark:[--slider-fill:var(--color-gray-600)]"
		style:background-image={`linear-gradient(to right, var(--slider-fill) ${progress}%, transparent ${progress}%)`}
		on:change={commit}
	/>
	<div class="flex items-center justify-between gap-3 text-gray-500">
		<span>{budget.min.toLocaleString()}–{budget.max.toLocaleString()}</span>
		<input
			type="number"
			min={budget.min}
			max={budget.max}
			step={budget.step}
			bind:value={current}
			{disabled}
			aria-label={`${control.label} tokens`}
			aria-invalid={invalid}
			on:change={commit}
			class="w-24 rounded-lg bg-gray-50/70 px-2 py-1 text-right outline-hidden dark:bg-white/5"
		/>
	</div>
	{#if invalid}<p class="text-[0.6875rem] text-red-500">请输入范围内、符合步长的整数。</p>{/if}
	<button
		type="button"
		disabled={disabled || value === ''}
		on:click={() => dispatch('change', '')}
		class="text-[0.6875rem] text-gray-500 enabled:hover:text-gray-900 dark:text-gray-400 dark:enabled:hover:text-gray-100"
		>{$i18n.t('Default')}</button
	>
</div>

<style>
	.control-slider::-webkit-slider-thumb {
		appearance: none;
		width: 0.9375rem;
		height: 1.1875rem;
		border-radius: 0.25rem;
		background: white;
		box-shadow: 0 1px 3px #0003;
	}
	.control-slider::-moz-range-thumb {
		width: 0.9375rem;
		height: 1.1875rem;
		border: 0;
		border-radius: 0.25rem;
		background: white;
		box-shadow: 0 1px 3px #0003;
	}
</style>
