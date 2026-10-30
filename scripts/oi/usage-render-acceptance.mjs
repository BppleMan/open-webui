import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const root = process.cwd(); // Run from work/open-webui.
const require = createRequire(path.join(root, 'package.json'));
const { compile } = require('svelte/compiler');
const { build } = require('esbuild');
const bundle = await build({
	stdin: {
		contents: `import { render } from 'svelte/server';
      import Component from './src/lib/components/chat/MessageUsageOverview.svelte';
      export default (props) => render(Component, { props }).body;`,
		resolveDir: root
	},
	bundle: true,
	write: false,
	format: 'esm',
	platform: 'node',
	alias: { $lib: path.join(root, 'src/lib') },
	define: { APP_VERSION: '"review"', APP_BUILD_HASH: '"review"', APP_BUILD_CHANNEL: '"review"' },
	plugins: [
		{
			name: 'svelte-ssr-review',
			setup(builder) {
				builder.onLoad({ filter: /\.svelte$/ }, async ({ path: filename }) => ({
					contents: compile(await readFile(filename, 'utf8'), {
						filename,
						generate: 'server',
						css: 'injected'
					}).js.code,
					loader: 'js',
					resolveDir: path.dirname(filename)
				}));
			}
		}
	]
});
const { default: renderOverview } = await import(
	'data:text/javascript;base64,' + Buffer.from(bundle.outputFiles[0].text).toString('base64')
);
const data = {
	messageId: 'after-import',
	status: '已完成',
	abnormal: false,
	callCount: 1,
	durationMs: 1000,
	metrics: [],
	actual: '0.03',
	estimated: '0',
	hasActual: true,
	hasEstimate: false,
	unknown: 0,
	snapshot: {
		actual_usd: '0.03',
		estimated_usd: '0',
		unknown_calls: 0,
		unknown_baselines: 1,
		incomplete: true,
		call_count: 1
	}
};
const body = renderOverview({ data });
const cumulative = body.match(/<div\s+data-usage-field="cumulative-cost"[\s\S]*?<\/dd>/)?.[0] || '';
const plain = cumulative
	.replace(/<[^>]+>/g, ' ')
	.replace(/\s+/g, ' ')
	.trim();
if (!/未知|待确认|不完整/.test(plain)) throw new Error('Missing unknown imported-cost indicator');
data.snapshot = { ...data.snapshot, unknown_baselines: 0, incomplete: false };
const settled =
	renderOverview({ data }).match(/<div\s+data-usage-field="cumulative-cost"[\s\S]*?<\/dd>/)?.[0] ||
	'';
if (/待确认|不完整/.test(settled))
	throw new Error('Known zero must remain distinct from missing cost');
