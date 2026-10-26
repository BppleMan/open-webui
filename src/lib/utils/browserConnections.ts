import { WEBUI_API_BASE_URL } from '$lib/constants';
import { oiApi } from '$lib/apis/customization';

const active = new Map<string, AbortController>();

export async function browserConfiguration(connectionId: string, revision?: number) {
	const response = await fetch(
		`${WEBUI_API_BASE_URL}/connections/${encodeURIComponent(connectionId)}/browser-config${revision == null ? '' : `?revision=${revision}`}`,
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
			data.model.connection.revision
		);
		const path = data.path;
		if (
			typeof path !== 'string' ||
			!/^\/(?:chat\/completions|responses|openai\/deployments\/[^/]+\/chat\/completions)(?:\?|$)/.test(
				path
			) ||
			path.includes('..')
		)
			throw new Error('Invalid endpoint');
		const response = await fetch(connection.base_url.replace(/\/$/, '') + path, {
			method: 'POST',
			headers: { ...connection.headers, 'Content-Type': 'application/json' },
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
		if (!response.ok || !data.form_data.stream) {
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
				body: JSON.stringify({ error: { message: '浏览器连接请求失败或已中断' } })
			});
		else socket.emit(data.channel, { error: true });
	} finally {
		active.delete(data.channel);
		socket.emit(data.channel, { done: true });
	}
}

export async function discoverBrowserModels(connection: any) {
	const config = await browserConfiguration(connection.id, connection.revision);
	const response = await fetch(config.base_url.replace(/\/$/, '') + '/models', {
		headers: { ...config.headers, Accept: 'application/json' },
		credentials: 'omit',
		redirect: 'error',
		cache: 'no-store'
	});
	if (!response.ok) throw new Error(`浏览器模型发现失败（HTTP ${response.status}）`);
	const body = await response.json();
	const models = Array.isArray(body) ? body : body.data;
	if (!Array.isArray(models)) throw new Error('供应商未返回有效模型列表');
	return oiApi(`/connections/${connection.id}/browser-models`, 'POST', {
		revision: config.revision,
		models
	});
}
