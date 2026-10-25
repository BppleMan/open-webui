import { WEBUI_API_BASE_URL } from '$lib/constants';
export async function oiApi(path: string, method = 'GET', body?: unknown) {
	const response = await fetch(`${WEBUI_API_BASE_URL}${path}`, {
		method,
		headers: { Authorization: `Bearer ${localStorage.token}`, 'Content-Type': 'application/json' },
		...(body === undefined ? {} : { body: JSON.stringify(body) })
	});
	const result = await response.json().catch(() => ({}));
	if (!response.ok)
		throw Object.assign(
			new Error(typeof result.detail === 'string' ? result.detail : `HTTP ${response.status}`),
			{ status: response.status, detail: result.detail }
		);
	return result;
}
