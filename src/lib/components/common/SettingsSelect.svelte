<script lang="ts">
	import { afterUpdate, onMount } from 'svelte';
	import { Select } from 'bits-ui';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Check from '$lib/components/icons/Check.svelte';

	export let value: string | number | boolean = '';
	export let id = '';
	export let name = '';
	export let ariaLabel = '';
	export let placeholder = '';
	export let required = false;
	export let disabled = false;
	export let className = 'w-fit max-w-full';
	export let selectClassName = '';

	let nativeSelect: HTMLSelectElement;
	let trigger: HTMLButtonElement | null = null;
	let invalid = false;
	let signature = '';
	let options: {
		key: string;
		value: typeof value;
		label: string;
		disabled: boolean;
		group: string;
		hidden: boolean;
	}[] = [];
	// Keep the original option slots, typed values, change events and form validation.
	// Bits UI handles focus, keyboard navigation, typeahead and portal positioning.
	function readOptions() {
		if (!nativeSelect) return;
		const next = Array.from(nativeSelect.options).map((option, index) => ({
			key: String(index + 1),
			value:
				'__value' in option
					? (option as HTMLOptionElement & { __value: typeof value }).__value
					: option.value,
			label: option.label,
			disabled:
				option.disabled ||
				(option.parentElement instanceof HTMLOptGroupElement && option.parentElement.disabled),
			group: option.parentElement instanceof HTMLOptGroupElement ? option.parentElement.label : '',
			hidden: option.hidden
		}));
		const nextSignature = JSON.stringify(next);
		if (signature !== nextSignature) {
			signature = nextSignature;
			options = next;
		}
	}
	onMount(() => {
		readOptions();
		const observer = new MutationObserver(readOptions);
		observer.observe(nativeSelect, {
			subtree: true,
			childList: true,
			attributes: true,
			characterData: true
		});
		return () => observer.disconnect();
	});
	afterUpdate(readOptions);
	$: selected = options.find((option) => Object.is(option.value, value));
	function selectOption(key: string) {
		const option = options.find((option) => option.key === key);
		if (!option || option.disabled || Object.is(value, option.value)) return;
		nativeSelect.selectedIndex = Number(key) - 1;
		invalid = false;
		nativeSelect.dispatchEvent(new Event('change', { bubbles: true }));
	}
</script>

<div class="relative inline-flex min-w-0 {className}">
	<select
		{...$$restProps}
		bind:this={nativeSelect}
		bind:value
		name={name || undefined}
		{required}
		{disabled}
		tabindex="-1"
		aria-hidden="true"
		class="sr-only"
		on:change
		on:invalid|preventDefault={() => {
			invalid = true;
			trigger?.focus();
		}}
	>
		<slot />
	</select>
	<Select.Root
		type="single"
		value={selected?.key ?? ''}
		{disabled}
		items={options.map((o) => ({ value: o.key, label: o.label, disabled: o.disabled || o.hidden }))}
		onValueChange={selectOption}
	>
		<Select.Trigger
			{...$$restProps}
			bind:ref={trigger}
			id={id || undefined}
			aria-label={ariaLabel || undefined}
			aria-invalid={invalid || undefined}
			aria-required={required || undefined}
			title={placeholder || undefined}
			class="focus-ring flex h-7 w-full min-w-0 items-center justify-between gap-2 rounded-lg border border-gray-100/50 bg-gray-50/60 px-2.5 text-left text-xs text-gray-700 transition-colors hover:bg-gray-100/80 disabled:opacity-50 dark:border-white/[0.04] dark:bg-white/[0.04] dark:text-gray-300 dark:hover:bg-white/[0.08] {selectClassName}"
		>
			<span class="truncate">{selected?.label || placeholder}</span>
			<ChevronDown className="size-3.5 shrink-0 text-gray-400 dark:text-gray-500" strokeWidth="2" />
		</Select.Trigger>
		<Select.Portal>
			<Select.Content
				sideOffset={5}
				align="end"
				collisionPadding={8}
				class="z-[10000] min-w-[var(--bits-select-anchor-width)] max-w-[calc(100vw-1rem)] rounded-xl border border-gray-200/70 bg-white p-1 shadow-lg outline-hidden dark:border-gray-700/60 dark:bg-gray-850"
				style="max-height: min(20rem, var(--bits-select-content-available-height)); overflow-y: auto; overscroll-behavior: contain;"
			>
				{#each options as option, index (option.key)}
					{#if !option.hidden}
						{#if option.group && (index === 0 || options[index - 1].group !== option.group)}
							<div class="px-2.5 pb-1 pt-2 text-[0.6875rem] font-medium text-gray-400">
								{option.group}
							</div>
						{/if}
						<Select.Item
							value={option.key}
							label={option.label}
							disabled={option.disabled}
							class="flex min-h-8 cursor-default select-none items-center justify-between gap-4 rounded-lg px-2.5 py-1.5 text-xs text-gray-700 outline-hidden data-[highlighted]:bg-gray-100 data-[disabled]:pointer-events-none data-[disabled]:opacity-40 dark:text-gray-200 dark:data-[highlighted]:bg-white/10"
						>
							<span class="min-w-0 break-words">{option.label}</span>
							<span class="size-3.5 shrink-0"
								>{#if selected?.key === option.key}<Check className="size-3.5" />{/if}</span
							>
						</Select.Item>
					{/if}
				{/each}
			</Select.Content>
		</Select.Portal>
	</Select.Root>
</div>
