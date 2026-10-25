import { writable } from 'svelte/store';
import { WEBUI_API_BASE_URL } from '$lib/constants';

export const sessionRecoveryRequired = writable(false);
let renewal: Promise<any> | null = null;

export function tokenExpiry(token: string): number | null {
	try {
		const segment = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
		const value = JSON.parse(atob(segment)).exp;
		return typeof value === 'number' ? value : null;
	} catch {
		return null;
	}
}

export function isStaleCredential(attempted: string, current: string | null): boolean {
	return attempted !== current;
}

export async function renewSession() {
	if (renewal) return renewal;
	const attempted = localStorage.getItem('token');
	if (!attempted) return null;
	renewal = (async () => {
		const response = await fetch(`${WEBUI_API_BASE_URL}/auths/refresh`, {
			method: 'POST',
			credentials: 'include',
			headers: { Authorization: `Bearer ${attempted}` }
		});
		// Another tab may have completed a new sign-in while this request was in flight.
		if (isStaleCredential(attempted, localStorage.getItem('token'))) return null;
		if (response.status === 401) {
			sessionRecoveryRequired.set(true);
			return null;
		}
		if (!response.ok) return null; // Transport/server errors never revoke a session.
		const session = await response.json();
		if (isStaleCredential(attempted, localStorage.getItem('token'))) return null;
		localStorage.setItem('token', session.token);
		sessionRecoveryRequired.set(false);
		return session;
	})()
		.catch(() => null)
		.finally(() => {
			renewal = null;
		});
	return renewal;
}
