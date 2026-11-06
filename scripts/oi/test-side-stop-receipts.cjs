const fs = require('fs'),
	cp = require('child_process'),
	ts = require('typescript');
const base = process.cwd();
const source = fs.readFileSync(base + '/src/lib/components/chat/ChatTabs/SideChat.svelte', 'utf8');
const sendStop = source.slice(
	source.indexOf('\tconst send = async'),
	source.indexOf('\n\t$: if (!sideChatAllowed)', source.indexOf('\tconst send = async'))
);
const receive = source.slice(
	source.indexOf('\tconst receive ='),
	source.indexOf('\n\tconst reconnect =')
);
const permission = source
	.slice(source.indexOf('\t$: if (!sideChatAllowed)'), source.indexOf('\n\tonMount('))
	.replace('$:', '');
const mount = source.slice(source.indexOf('\tonMount(() => {'), source.indexOf('\n</script>'));
const api = fs.readFileSync(base + '/src/lib/apis/openai/index.ts', 'utf8');
const errorFn = api.slice(
	api.indexOf('export const getErrorMessage'),
	api.indexOf('export const getOpenAIConfig')
);
const generateFn = api.slice(
	api.indexOf('export const generateOpenAIChatCompletion'),
	api.indexOf('export const synthesizeOpenAISpeech')
);
const compile = (s) =>
	ts.transpileModule(s.replaceAll('export const ', 'const '), {
		compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None }
	}).outputText;
const snapshots = [];
function fixture(mode = 'accepted') {
	let release, enter, cleanup;
	const gate = new Promise((r) => (release = r)),
		entered = new Promise((r) => (enter = r));
	let requests = 0,
		stops = 0,
		active = false,
		ids = 0,
		stopFailure = false,
		usesRealApi = false;
	const env = {
		sideChatAllowed: true,
		draft: 'question',
		files: [],
		busy: false,
		atSelectedModel: undefined,
		selectedModels: ['oi:m'],
		sideModelId: 'oi:m',
		$socket: { connected: true, id: 'socket', on() {}, off() {} },
		id: 'temporary:socket:side',
		tick: async () => {},
		input: { uploadPendingFiles: async () => true, setText() {}, focus() {} },
		sendEpoch: 0,
		request: null,
		pendingDispatches: new Set(),
		contextReady: mode !== 'context',
		context: [],
		sourceChatId: 'parent',
		sourceTemporaryId: undefined,
		oiTemporaryId: 'stable',
		oiApi: async () => {
			enter();
			await gate;
			return { messages: [] };
		},
		disposed: false,
		messages: [],
		uuidv4: () => `message${++ids}`,
		nativeMessageSnapshot: (x) => structuredClone(x),
		isRasterImageContentType: () => false,
		selectedToolIds: [],
		selectedSkillIds: [],
		selectedFilterIds: [],
		autoScroll: false,
		scroll: async () => {},
		localStorage: { token: 'synthetic' },
		generateOpenAIChatCompletion: async () => {
			requests++;
			active = true;
			enter();
			if (mode === 'pending') await gate;
			if (mode === 'failure') {
				active = false;
				throw Error('Synthetic rejected request');
			}
			return { task_ids: ['task'] };
		},
		$models: [{ id: 'oi:m' }],
		params: {},
		toolApprovalMode: 'full',
		$config: { features: {} },
		$selectedTerminalId: null,
		$toolServers: [],
		$terminalServers: [],
		webSearchEnabled: false,
		imageGenerationEnabled: false,
		codeInterpreterEnabled: false,
		$settings: {},
		system: '',
		getErrorMessage: String,
		error: '',
		interactions: [],
		cancelInteractions() {},
		stopTasksByChatId: async () => {
			stops++;
			if (stopFailure) throw Error('synthetic stop outage');
			active = false;
			return { status: true };
		},
		$i18n: { t: (x) => x },
		applyResponseStreamEvent: (_o, data) => data.output ?? _o,
		initialPrompt: '',
		openTemporaryChatIds: { update: (f) => f(new Set(['temporary:socket:side'])) },
		onMount: (fn) => {
			cleanup = fn();
		},
		reconnect: async () => {},
		WEBUI_BASE_URL: 'http://synthetic.invalid',
		fetch: async () => {
			requests++;
			active = true;
			enter();
			return new Response('{"status":true,"task_ids":[', {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}
	};
	if (mode === 'lost-ack') {
		env.generateOpenAIChatCompletion = new Function(
			'env',
			`with(env){${compile(errorFn + generateFn)}; return generateOpenAIChatCompletion;}`
		)(env);
		usesRealApi = true;
	}
	const methods = new Function(
		'env',
		`with(env){${compile(receive + sendStop + mount)};return{send,stop,receive,permissionChanged:()=>{${compile(permission)}}};}`
	)(env);
	const state = () => ({
		requests,
		stops,
		active,
		busy: env.busy,
		request_null: env.request === null,
		done: env.messages.at(-1)?.done,
		disposed: env.disposed,
		error: env.error,
		uses_real_fetch_parser: usesRealApi
	});
	return {
		env,
		methods,
		state,
		release,
		entered,
		close: () => cleanup(),
		failStop: (v) => (stopFailure = v),
		terminal: () => {
			active = false;
			methods.receive({
				chat_id: env.id,
				message_id: env.messages.at(-1).id,
				data: {
					type: 'chat:tasks:cancel',
					data: {
						output: [{ type: 'message', content: [{ type: 'output_text', text: 'partial' }] }],
						oi_native_state: 'final-capsule'
					}
				}
			});
		}
	};
}
const flush = async () => {
	for (let i = 0; i < 10; i++) await Promise.resolve();
};
const record = (name, h, assertion) => {
	const s = h.state();
	snapshots.push({ case: name, ...s, passed: assertion(s) });
};
(async () => {
	let h = fixture();
	await h.methods.send();
	await h.methods.stop();
	record('accepted-stop', h, (s) => s.stops === 1 && !s.active && s.busy && !s.done);
	await h.methods.stop();
	record('repeat-stop-before-snapshot', h, (s) => s.stops === 2 && s.busy && !s.done);
	h.terminal();
	record(
		'authoritative-final-snapshot',
		h,
		(s) => !s.busy && s.done && h.env.messages.at(-1).oi_native_state === 'final-capsule'
	);
	await h.methods.stop();
	record('repeat-stop-after-snapshot', h, (s) => s.stops === 2);
	h.env.draft = 'next';
	await h.methods.send();
	await h.methods.stop();
	record('next-turn-stop', h, (s) => s.requests === 2 && s.stops === 3 && s.busy && !s.done);
	h = fixture('pending');
	let sending = h.methods.send();
	await h.entered;
	const stopping = h.methods.stop();
	h.release();
	await Promise.all([sending, stopping]);
	record('http-pending-stop', h, (s) => s.stops === 1 && s.busy && !s.active);
	h = fixture('context');
	sending = h.methods.send();
	await h.entered;
	await h.methods.stop();
	h.release();
	await sending;
	record('context-preparation-stop', h, (s) => s.requests === 0 && s.stops === 0 && !s.busy);
	await h.methods.send();
	record('new-send-after-preparation-stop', h, (s) => s.requests === 1 && s.busy);
	h.terminal();
	h = fixture();
	await h.methods.send();
	h.close();
	await flush();
	record('close-after-acceptance', h, (s) => s.disposed && s.stops === 1 && !s.active);
	h = fixture('context');
	sending = h.methods.send();
	await h.entered;
	h.close();
	h.release();
	await sending;
	await flush();
	record('close-during-preparation', h, (s) => s.disposed && s.requests === 0 && s.stops === 0);
	h = fixture();
	await h.methods.send();
	h.failStop(true);
	await h.methods.stop();
	record(
		'stop-outage-retains-block',
		h,
		(s) => s.active && s.busy && s.error.includes('stop outage')
	);
	h.failStop(false);
	await h.methods.stop();
	record('retry-stop-after-outage', h, (s) => s.stops === 2 && !s.active && s.busy);
	h = fixture('failure');
	await h.methods.send();
	h.close();
	await flush();
	record(
		'explicit-generation-failure-control',
		h,
		(s) => !s.active && !s.busy && s.done && s.stops === 1
	);
	h = fixture();
	await h.methods.send();
	h.env.sideChatAllowed = false;
	h.methods.permissionChanged();
	await flush();
	record('permission-loss-stop', h, (s) => s.stops === 1 && !s.active);
	h = fixture('lost-ack');
	await h.methods.send();
	record(
		'accepted-but-truncated-http-response',
		h,
		(s) => s.requests === 1 && s.active && s.done && !s.busy && s.uses_real_fetch_parser
	);
	h.close();
	await flush();
	record('close-after-lost-acceptance', h, (s) => s.stops === 1 && !s.active);
	h = fixture('lost-ack');
	await h.methods.send();
	h.env.sideChatAllowed = false;
	h.methods.permissionChanged();
	await flush();
	record('permission-loss-after-lost-acceptance', h, (s) => s.stops === 1 && !s.active);
	for (const type of ['chat:completion', 'chat:message:error']) {
		h = fixture();
		await h.methods.send();
		h.methods.receive({
			chat_id: h.env.id,
			message_id: h.env.messages.at(-1).id,
			data: {
				type,
				data: {
					done: true,
					error: type === 'chat:message:error' ? 'synthetic terminal error' : undefined
				}
			}
		});
		h.close();
		await flush();
		record(type + '-clears-dispatch', h, (s) => s.stops === 0 && !s.busy && s.done);
	}

	const out = {
		source: base,
		head: cp.execFileSync('git', ['-C', base, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
		cases: snapshots,
		passed: snapshots.filter((x) => x.passed).length,
		failed: snapshots.filter((x) => !x.passed).length,
		external_network_requests: 0
	};
	console.log(JSON.stringify(out, null, 2));
	if (out.failed) process.exitCode = 1;
})().catch((e) => {
	console.error(e);
	process.exitCode = 1;
});
