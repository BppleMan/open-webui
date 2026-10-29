import { WEBUI_API_BASE_URL } from '$lib/constants';
import { oiApi } from '$lib/apis/customization';

const active = new Map<string, AbortController>();

export async function browserConfiguration(
	connectionId: string,
	revision?: number,
	bindingId?: string
) {
	const query = new URLSearchParams();
	if (revision != null) query.set('revision', String(revision));
	if (bindingId) query.set('binding_id', bindingId);
	const response = await fetch(
		`${WEBUI_API_BASE_URL}/connections/${encodeURIComponent(connectionId)}/browser-config?${query}`,
		{
			headers: { Authorization: `Bearer ${localStorage.token}` },
			cache: 'no-store'
		}
	);
	if (!response.ok) throw new Error((await response.json()).detail || '浏览器连接不可用');
	return response.json();
}

export function cancelBrowserCompletion(channel?: string) {
	if (channel) active.get(channel)?.abort();
	else for (const controller of active.values()) controller.abort();
}

export async function runBrowserCompletion(data: any, socket: any, callback: (value: any) => void) {
	const controller = new AbortController();
	active.set(data.channel, controller);
	let acknowledged = false;
	try {
		if (data.model?.connection?.transport !== 'browser')
			throw new Error('Unregistered browser connection');
		const connection = await browserConfiguration(
			data.model.connection.id,
			data.model.connection.revision,
			data.model.id
		);
		const request = browserRequest(data, connection);
		const response = await fetch(request.url, {
			method: 'POST',
			headers: request.headers,
			body: JSON.stringify(data.form_data),
			signal: controller.signal,
			credentials: 'omit',
			redirect: 'error'
		});
		const headers = Object.fromEntries(
			['content-type', 'retry-after', 'x-request-id'].flatMap((key) =>
				response.headers.has(key) ? [[key, response.headers.get(key)]] : []
			)
		);
		if (!response.ok || !data.stream) {
			callback({ status_code: response.status, headers, body: await response.text() });
			acknowledged = true;
			return;
		}
		callback({ status_code: response.status, headers });
		acknowledged = true;
		const reader = response.body!.getReader();
		const decoder = new TextDecoder();
		while (true) {
			const { value, done } = await reader.read();
			if (done) break;
			const chunk = decoder.decode(value, { stream: true });
			if (chunk) socket.emit(data.channel, chunk);
		}
		const tail = decoder.decode();
		if (tail) socket.emit(data.channel, tail);
	} catch {
		if (!acknowledged)
			callback({
				status_code: 502,
				body: JSON.stringify({
					error: { message: '浏览器连接失败：请检查网络、服务的跨域（CORS）支持或是否已停止请求' }
				})
			});
		else socket.emit(data.channel, { error: true });
	} finally {
		active.delete(data.channel);
		socket.emit(data.channel, { done: true });
	}
}

export function browserRequest(data: any, connection: any) {
	const url = connection.endpoints?.[data.stream ? 'stream' : 'generate'];
	if (typeof url !== 'string' || data.url !== url || data.protocol !== connection.protocol)
		throw new Error('连接模式或请求地址已改变，请重新发送');
	const protocolHeaders = Object.fromEntries(
		Object.entries(data.protocol_headers || {}).filter(([key]) =>
			['x-rp-client', 'x-rp-user-id', 'x-rp-chat-id', 'anthropic-version'].includes(
				key.toLowerCase()
			)
		)
	);
	return {
		url,
		headers: { ...connection.headers, ...protocolHeaders, 'Content-Type': 'application/json' }
	};
}

export function modelPage(body: any, protocol: string) {
	const native = ['gemini', 'gemini-interactions', 'ollama'].includes(protocol);
	const models = Array.isArray(body) ? body : native ? body.models : body.data;
	if (!Array.isArray(models) || models.some((model) => !model || typeof model !== 'object'))
		throw new Error('供应商未返回有效模型列表');
	let next: [string, string] | null = null;
	if (protocol.startsWith('gemini') && body.nextPageToken) next = ['pageToken', body.nextPageToken];
	else if (protocol === 'anthropic' && body.has_more) next = ['after_id', body.last_id];
	if (next && (typeof next[1] !== 'string' || !next[1])) throw new Error('模型分页游标无效');
	return { models, next };
}

export async function discoverBrowserModels(connection: any) {
	const config = await browserConfiguration(connection.id, connection.revision);
	const models: any[] = [],
		seen = new Set<string>();
	let params: [string, string] | null = null;
	for (let page = 0; page < 100; page++) {
		const url = new URL(config.endpoints.models);
		if (params) url.searchParams.set(...params);
		const response = await fetch(url, {
			headers: { ...config.headers, Accept: 'application/json' },
			credentials: 'omit',
			redirect: 'error',
			cache: 'no-store'
		});
		if (!response.ok) throw new Error(`浏览器模型发现失败（HTTP ${response.status}）`);
		const parsed = modelPage(await response.json(), config.protocol);
		models.push(...parsed.models);
		if (models.length > 10000) throw new Error('模型目录过大，现有配置已保留');
		if (!parsed.next)
			return oiApi(`/connections/${connection.id}/browser-models`, 'POST', {
				revision: config.revision,
				models
			});
		const cursor = JSON.stringify(parsed.next);
		if (seen.has(cursor)) throw new Error('模型分页重复，现有配置已保留');
		seen.add(cursor);
		params = parsed.next;
	}
	throw new Error('模型分页超出限制，现有配置已保留');
}
