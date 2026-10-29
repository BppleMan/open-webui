<script lang="ts">
	import ArrowUpTray from '$lib/components/icons/ArrowUpTray.svelte';
	import ArrowDownTray from '$lib/components/icons/ArrowDownTray.svelte';
	import Database from '$lib/components/icons/Database.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import Hashtag from '$lib/components/icons/Hashtag.svelte';
	import CurrencyDollar from '$lib/components/icons/CurrencyDollar.svelte';
	import ClockRotateRight from '$lib/components/icons/ClockRotateRight.svelte';
	import Link from '$lib/components/icons/Link.svelte';
	import InfoCircle from '$lib/components/icons/InfoCircle.svelte';
	export let kind: string;
	export let className = 'size-3';
	const icons = {
		input_tokens: ArrowUpTray,
		output_tokens: ArrowDownTray,
		cache_read_tokens: Database,
		cache_write_tokens: Database,
		reasoning_tokens: Sparkles,
		total_tokens: Hashtag,
		cost: CurrencyDollar,
		cumulative: CurrencyDollar,
		duration: ClockRotateRight,
		source: Link,
		calls: Hashtag,
		status: InfoCircle
	};
	const tones: Record<string, string> = {
		input_tokens: 'text-sky-600 dark:text-sky-400',
		output_tokens: 'text-emerald-600 dark:text-emerald-400',
		cache_read_tokens: 'text-violet-500 dark:text-violet-400',
		cache_write_tokens: 'text-violet-500 dark:text-violet-400',
		reasoning_tokens: 'text-violet-500 dark:text-violet-400',
		cost: 'text-amber-600 dark:text-amber-400',
		cumulative: 'text-amber-600 dark:text-amber-400',
		source: 'text-sky-600 dark:text-sky-400',
		calls: 'text-sky-600 dark:text-sky-400',
		status: 'text-amber-700 dark:text-amber-400'
	};
</script>

<span aria-hidden="true" class="inline-flex shrink-0 items-center">
	<svelte:component
		this={icons[kind as keyof typeof icons] ?? Hashtag}
		className={`${className} ${tones[kind] ?? 'text-gray-400 dark:text-gray-500'}`}
	/>
</span>
