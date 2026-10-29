<script lang="ts">
	import { getContext, onDestroy, tick } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';
	import { Tabs } from 'bits-ui';
	import { replaceOutputText } from '$lib/utils/outputEditing';

	import { basicSetup, EditorView } from 'codemirror';
	import { keymap } from '@codemirror/view';
	import { Compartment, EditorState } from '@codemirror/state';
	import { json } from '@codemirror/lang-json';
	import { indentWithTab } from '@codemirror/commands';
	import { indentUnit } from '@codemirror/language';
	import { oneDark } from '@codemirror/theme-one-dark';

	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext<typeof import('$lib/i18n').default>('i18n');

	export let output: any[] = [];
	export let compact = false;
	export let valid = true;
	$: valid = !jsonError;
	export let onChange: (output: any[]) => void = () => {};

	let viewMode: 'visual' | 'json' = 'visual';
	let jsonError = '';
	let activeTab = 'message';
	let cmOutput: any[] | null = null;
	let syncingCodeMirror = false;

	// --- CodeMirror ---
	let cmContainer: HTMLDivElement;
	let cmEditor: EditorView | null = null;
	let editorTheme = new Compartment();

	function initCodeMirror() {
		if (cmEditor || !cmContainer?.isConnected || viewMode !== 'json') return;
		cmOutput = output;
		const isDark = document.documentElement.classList.contains('dark');
		cmEditor = new EditorView({
			state: EditorState.create({
				doc: JSON.stringify(output, null, 2),
				extensions: [
					basicSetup,
					keymap.of([indentWithTab]),
					indentUnit.of('  '),
					json(),
					editorTheme.of(isDark ? oneDark : []),
					EditorView.theme({
						'&': { fontSize: '13px' },
						'.cm-content': { fontFamily: 'ui-monospace, monospace' },
						'.cm-scroller': { maxHeight: '320px', overflow: 'auto' },
						'&.cm-focused': { outline: 'none' }
					}),
					EditorView.updateListener.of((e) => {
						if (e.docChanged && !syncingCodeMirror) {
							try {
								const parsed = JSON.parse(e.state.doc.toString());
								if (Array.isArray(parsed)) {
									jsonError = '';
									cmOutput = parsed;
									output = parsed;
									onChange(output);
								} else {
									jsonError = 'Must be a JSON array';
								}
							} catch {
								jsonError = 'Invalid JSON';
							}
						}
					})
				]
			}),
			parent: cmContainer
		});
	}

	function destroyCodeMirror() {
		if (cmEditor) {
			cmEditor.destroy();
			cmEditor = null;
			cmOutput = null;
		}
	}

	async function switchToJson() {
		viewMode = 'json';
		await tick();
		initCodeMirror();
	}

	function switchToVisual() {
		if (jsonError) return;
		destroyCodeMirror();
		viewMode = 'visual';
	}

	onDestroy(() => destroyCodeMirror());

	// --- Display items ---

	interface DisplayItem {
		type: 'message' | 'reasoning' | 'function_call' | 'code_interpreter' | 'openai_tool';
		indices: number[];
		item: any;
		outputItem?: any;
	}

	function buildDisplayItems(items: any[]): DisplayItem[] {
		const result: DisplayItem[] = [];
		const outputByCallId: Record<string, { item: any; index: number }> = {};

		for (let i = 0; i < items.length; i++) {
			if (items[i]?.type === 'function_call_output') {
				outputByCallId[items[i].call_id] = { item: items[i], index: i };
			}
		}

		for (let i = 0; i < items.length; i++) {
			const item = items[i];
			const t = item?.type ?? '';
			if (t === 'message') {
				result.push({ type: 'message', indices: [i], item });
			} else if (t === 'reasoning') {
				result.push({ type: 'reasoning', indices: [i], item });
			} else if (t === 'function_call') {
				const paired = outputByCallId[item.call_id];
				result.push({
					type: 'function_call',
					indices: paired ? [i, paired.index] : [i],
					item,
					outputItem: paired?.item
				});
			} else if (t === 'function_call_output') {
				// grouped with function_call
			} else if (t === 'open_webui:code_interpreter') {
				result.push({ type: 'code_interpreter', indices: [i], item });
			} else if (['web_search_call', 'file_search_call', 'computer_call'].includes(t)) {
				result.push({ type: 'openai_tool', indices: [i], item });
			}
		}
		return result;
	}

	$: displayItems = buildDisplayItems(output);
	$: editorTabs = [
		...(displayItems.some((item) => item.type === 'message')
			? [{ id: 'message', label: '正文' }]
			: []),
		...(displayItems.some((item) => item.type === 'reasoning')
			? [{ id: 'reasoning', label: '思考' }]
			: []),
		...(displayItems.some((item) => !['message', 'reasoning'].includes(item.type))
			? [{ id: 'tools', label: '工具' }]
			: []),
		{ id: 'json', label: 'JSON' }
	];
	$: if (compact && !editorTabs.some((tab) => tab.id === activeTab)) {
		void selectEditorTab(editorTabs[0].id);
	}
	async function selectEditorTab(value: string) {
		if (jsonError && value !== 'json') {
			activeTab = 'json';
			return;
		}
		activeTab = value;
		if (value === 'json') await switchToJson();
		else switchToVisual();
	}
	// External undo/revision loads must also replace the JSON editor, including an invalid draft.
	$: if (cmEditor && output !== cmOutput) {
		cmOutput = output;
		jsonError = '';
		syncingCodeMirror = true;
		cmEditor.dispatch({
			changes: { from: 0, to: cmEditor.state.doc.length, insert: JSON.stringify(output, null, 2) }
		});
		syncingCodeMirror = false;
	}

	// --- Helpers ---

	function getMessageText(item: any): string {
		return (item.content ?? [])
			.filter((p: any) => p && (p.type === 'output_text' || 'text' in p))
			.map((p: any) => p.text ?? '')
			.join('');
	}

	function updateMessageText(idx: number, text: string) {
		output = replaceOutputText(output, idx, 'content', text, 'output_text');
		onChange(output);
	}

	function getReasoningText(item: any): string {
		return (item.summary ?? item.content ?? [])
			.filter((p: any) => p && 'text' in p)
			.map((p: any) => p.text ?? '')
			.join('');
	}

	function updateReasoningText(idx: number, text: string) {
		const key = output[idx].summary ? 'summary' : 'content';
		output = replaceOutputText(output, idx, key, text, key === 'summary' ? 'summary_text' : 'text');
		onChange(output);
	}

	function deleteIndices(indices: number[]) {
		const rm = new Set(indices);
		output = output.filter((_, i) => !rm.has(i));
		onChange(output);
	}

	function formatArgs(args: any): string {
		if (!args) return '';
		try {
			return typeof args === 'string' ? args : JSON.stringify(args, null, 2);
		} catch {
			return String(args);
		}
	}

	function resizeEl(el: HTMLTextAreaElement) {
		const c = document.getElementById('messages-container');
		const s = c?.scrollTop;
		const editor = el.closest<HTMLElement>('[data-testid="reply-editor-body"]');
		const editorScroll = editor?.scrollTop;
		el.style.height = '';
		el.style.height = `${el.scrollHeight}px`;
		if (c && s !== undefined) c.scrollTop = s;
		if (editor && editorScroll !== undefined) editor.scrollTop = editorScroll;
	}

	function autoResize(e: Event) {
		resizeEl(e.target as HTMLTextAreaElement);
	}

	/** Resize after programmatic revision/undo changes as well as user input. */
	function fitContent(el: HTMLTextAreaElement, _text: string) {
		resizeEl(el);
		return {
			update() {
				void tick().then(() => {
					if (el.isConnected) resizeEl(el);
				});
			}
		};
	}

	function getItemLabel(di: DisplayItem): string {
		switch (di.type) {
			case 'message':
				return 'Text';
			case 'reasoning':
				return 'Thought';
			case 'function_call':
				return di.item.name ?? 'Tool';
			case 'code_interpreter':
				return 'Code';
			case 'openai_tool': {
				const names: Record<string, string> = {
					web_search_call: 'Search',
					file_search_call: 'Files',
					computer_call: 'Computer'
				};
				return names[di.item.type] ?? di.item.type;
			}
			default:
				return 'Item';
		}
	}
</script>

{#snippet jsonEditor()}
	<div
		bind:this={cmContainer}
		class="w-full rounded-2xl overflow-hidden border border-gray-100 dark:border-gray-800"
	/>
	{#if jsonError}
		<div class="text-xs text-red-500 mt-1.5 px-1">{jsonError}</div>
	{/if}
{/snippet}

{#snippet visualEditor(items: DisplayItem[])}
	<!-- Visual editor: playground-style rows -->
	<div class={compact ? 'space-y-1' : 'space-y-2 p-2 pt-3'}>
		{#each items as di, idx}
			<div class="group flex {compact ? 'relative flex-col gap-0.5 pr-7' : 'gap-2'}">
				<!-- Role label -->
				{#if !compact}
					<div class="flex items-start {compact ? '' : 'pt-1.5'}">
						<div
							class="text-[0.6875rem] font-normal uppercase tracking-wide min-w-[4.5rem] text-gray-400 dark:text-gray-500"
						>
							{getItemLabel(di)}
						</div>
					</div>
				{/if}
				<!-- Content -->
				<div class="flex-1 min-w-0">
					{#if di.type === 'message'}
						<textarea
							aria-label={compact ? '回复正文' : $i18n.t('Message text...')}
							use:fitContent={getMessageText(di.item)}
							class="w-full bg-transparent outline-hidden focus-visible:outline-none! resize-none overflow-hidden text-[0.9375rem] {compact
								? 'p-0.5'
								: 'p-1.5'} rounded-lg"
							value={getMessageText(di.item)}
							on:input={(e) => {
								updateMessageText(di.indices[0], e.target.value);
								autoResize(e);
							}}
							placeholder={$i18n.t('Message text...')}
							rows="1"
						/>
					{:else if di.type === 'reasoning'}
						<textarea
							aria-label={compact ? '思考内容' : $i18n.t('Reasoning text...')}
							use:fitContent={getReasoningText(di.item)}
							class="w-full bg-transparent outline-hidden focus-visible:outline-none! resize-none overflow-hidden text-[0.9375rem] text-gray-500 dark:text-gray-400 {compact
								? 'p-0.5'
								: 'p-1.5'} rounded-lg"
							value={getReasoningText(di.item)}
							on:input={(e) => {
								updateReasoningText(di.indices[0], e.target.value);
								autoResize(e);
							}}
							placeholder={$i18n.t('Reasoning text...')}
							rows="1"
						/>
					{:else if di.type === 'function_call'}
						<div class="text-[0.9375rem] p-1.5 text-gray-500 dark:text-gray-400">
							{#if di.item.arguments}
								<pre
									class="text-xs font-mono whitespace-pre-wrap overflow-x-auto pb-0.5">{formatArgs(
										di.item.arguments
									)}</pre>
							{/if}
							{#if di.outputItem}
								<pre
									class="text-xs font-mono whitespace-pre-wrap overflow-x-auto mt-1 max-h-32 overflow-y-auto">{JSON.stringify(
										di.outputItem.output,
										null,
										2
									)}</pre>
							{/if}
						</div>
					{:else if di.type === 'code_interpreter'}
						<div class="text-[0.9375rem] p-1.5 text-gray-500 dark:text-gray-400">
							{#if di.item.code}
								<pre class="text-xs font-mono whitespace-pre overflow-x-auto">{di.item.code}</pre>
							{/if}
							{#if di.item.output}
								<pre
									class="text-xs font-mono whitespace-pre-wrap overflow-x-auto mt-1 max-h-32 overflow-y-auto">{typeof di
										.item.output === 'object'
										? JSON.stringify(di.item.output, null, 2)
										: di.item.output}</pre>
							{/if}
						</div>
					{:else if di.type === 'openai_tool'}
						<div class="text-[0.9375rem] p-1.5 text-gray-500 dark:text-gray-400">
							{#if di.item.action?.queries || di.item.queries}
								<span class="text-xs"
									>{(di.item.action?.queries ?? di.item.queries ?? []).join(', ')}</span
								>
							{/if}
						</div>
					{/if}
				</div>

				<!-- Delete -->
				<div class={compact ? 'absolute right-0 top-0' : 'pt-1.5'}>
					<button
						class="hover-reveal p-1 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition rounded-lg"
						aria-label={$i18n.t('Delete')}
						on:click={() => deleteIndices(di.indices)}
					>
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="2"
							stroke="currentColor"
							class="w-4 h-4"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M15 12H9m12 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
							/>
						</svg>
					</button>
				</div>
			</div>
		{/each}

		{#if items.length === 0}
			<div class="text-sm text-gray-400 dark:text-gray-500 italic px-1">
				{$i18n.t('No output items')}
			</div>
		{/if}
	</div>
{/snippet}

{#if compact}
	<Tabs.Root
		bind:value={activeTab}
		onValueChange={selectEditorTab}
		class="w-full"
		data-testid="reply-content-tabs"
	>
		<Tabs.List
			aria-label="回复编辑内容"
			class="flex items-center gap-1 border-b border-gray-200/70 pb-1 dark:border-white/10"
		>
			{#each editorTabs as tab}<Tabs.Trigger
					value={tab.id}
					disabled={Boolean(jsonError) && tab.id !== 'json'}
					class="focus-ring rounded-md px-2.5 py-1.5 text-xs text-gray-500 data-[state=active]:bg-gray-200/60 data-[state=active]:font-medium data-[state=active]:text-gray-900 dark:text-gray-400 dark:data-[state=active]:bg-white/10 dark:data-[state=active]:text-white"
					>{tab.label}</Tabs.Trigger
				>{/each}
		</Tabs.List>
		{#each editorTabs as tab}<Tabs.Content value={tab.id} class="pt-2 outline-hidden">
				{#if activeTab === tab.id}
					{#if tab.id === 'json'}{@render jsonEditor()}
					{:else}{@render visualEditor(
							displayItems.filter((item) =>
								tab.id === 'tools'
									? !['message', 'reasoning'].includes(item.type)
									: item.type === tab.id
							)
						)}{/if}
				{/if}
			</Tabs.Content>{/each}
	</Tabs.Root>
{:else}
	<div class="w-full relative">
		<!-- Mode toggle -->
		<div class={compact ? 'flex h-5 justify-end' : 'absolute -top-0.5 right-0.5 z-10'}>
			<Tooltip
				content={viewMode === 'visual'
					? $i18n.t('Switch to JSON editor')
					: $i18n.t('Switch to visual editor')}
			>
				<button
					class="text-xs px-2 py-0.5 rounded-full transition-all text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-200/50 dark:hover:bg-gray-700/50"
					on:click={() => (viewMode === 'visual' ? switchToJson() : switchToVisual())}
				>
					{viewMode === 'visual' ? $i18n.t('Visual') : 'JSON'}
				</button>
			</Tooltip>
		</div>

		{#if viewMode === 'json'}{@render jsonEditor()}{:else}{@render visualEditor(displayItems)}{/if}
	</div>
{/if}
