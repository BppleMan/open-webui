const fs = require('fs');
const assert = require('assert');
const ts = require('typescript');
const base = process.cwd();
const src = fs.readFileSync(base + '/src/lib/components/chat/ChatTabs/SideChat.svelte', 'utf8');
const snippet = src.slice(
	src.indexOf('\tconst send = async'),
	src.indexOf('\n\t$: if (!sideChatAllowed)', src.indexOf('\tconst send = async'))
);
const code = ts.transpileModule(snippet, {
	compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None }
}).outputText;
const create = (mode) => {
	let resume, entered;
	const gate = new Promise((r) => (resume = r)),
		waiting = new Promise((r) => (entered = r));
	let ids = 0,
		stops = 0,
		requests = 0;
	const env = {
		sideChatAllowed: true,
		draft: 'synthetic side question',
		files: [],
		busy: false,
		atSelectedModel: undefined,
		selectedModels: ['oi:m'],
		sideModelId: 'oi:m',
		$socket: { connected: true, id: 'socket' },
		id: 'temporary:socket:side',
		tick: async () => {},
		input: { uploadPendingFiles: async () => true, setText() {} },
		sendEpoch: 0,
		request: null,
		contextReady: mode !== 'context',
		context: [],
		sourceChatId: 'source',
		sourceTemporaryId: undefined,
		oiTemporaryId: 'side-stable',
		oiApi: async () => {
			entered();
			await gate;
			return { messages: [] };
		},
		disposed: false,
		messages: [],
		uuidv4: () => `id${++ids}`,
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
			entered();
			if (mode === 'pending') await gate;
			return { task_ids: ['running-background-task'] };
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
		cancelInteractions() {},
		stopTasksByChatId: async () => {
			stops++;
			return { status: true };
		},
		$i18n: { t: (x) => x }
	};
	const methods = new Function('env', `with(env){${code};return{send,stop};}`)(env);
	return { env, methods, resume, waiting, stops: () => stops, requests: () => requests };
};
(async () => {
	const observations = [];
	let h = create('accepted');
	await h.methods.send();
	assert.equal(h.env.request, null);
	assert.equal(h.env.busy, true);
	await h.methods.stop();
	observations.push({
		case: 'stop-after-http-accepted',
		stop_api_calls: h.stops(),
		provider_acceptance_calls: h.requests(),
		busy: h.env.busy,
		assistant_done: h.env.messages.at(-1).done,
		expected_stop_api_calls: 1
	});
	h = create('accepted');
	await h.methods.send();
	h.env.disposed = true;
	await h.methods.stop();
	assert.equal(h.stops(), 1);
	assert.equal(h.env.busy, true);
	h = create('pending');
	let sending = h.methods.send();
	await h.waiting;
	const stopping = h.methods.stop();
	h.resume();
	await Promise.all([sending, stopping]);
	observations.push({
		case: 'stop-while-http-pending-control',
		stop_api_calls: h.stops(),
		provider_acceptance_calls: h.requests(),
		busy: h.env.busy,
		expected_stop_api_calls: 1
	});
	h = create('context');
	sending = h.methods.send();
	await h.waiting;
	await h.methods.stop();
	h.resume();
	await sending;
	observations.push({
		case: 'stop-during-context-transfer-control',
		stop_api_calls: h.stops(),
		provider_acceptance_calls: h.requests(),
		busy: h.env.busy,
		expected_provider_acceptance_calls: 0
	});
	const out = {
		source: base,
		head: require('child_process')
			.execFileSync('git', ['-C', base, 'rev-parse', 'HEAD'], { encoding: 'utf8' })
			.trim(),
		observations,
		network_requests: 0
	};
	console.log(JSON.stringify(out, null, 2));
	assert.equal(
		observations[0].stop_api_calls,
		1,
		'Accepted background task must be cancelled after HTTP request promise has settled'
	);
})().catch((e) => {
	console.error(e.message);
	process.exitCode = 1;
});
