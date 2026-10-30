import { WEBUI_API_BASE_URL } from '$lib/constants';
export async function oiApi(
	path: string,
	method = 'GET',
	body?: unknown,
	options?: { etag?: string; onEtag?: (value: string) => void }
) {
	const response = await fetch(`${WEBUI_API_BASE_URL}${path}`, {
		method,
		headers: {
			Authorization: `Bearer ${localStorage.token}`,
			'Content-Type': 'application/json',
			...(options?.etag ? { 'If-None-Match': options.etag } : {})
		},
		...(body === undefined ? {} : { body: JSON.stringify(body) })
	});
	if (response.status === 304) return null;
	if (response.ok && response.headers.get('ETag')) options?.onEtag?.(response.headers.get('ETag')!);
	const result = await response.json().catch(() => ({}));
	if (!response.ok)
		throw Object.assign(
			new Error(typeof result.detail === 'string' ? result.detail : `HTTP ${response.status}`),
			{ status: response.status, detail: result.detail }
		);
	return result;
}
