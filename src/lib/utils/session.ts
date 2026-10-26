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
		if (typeof window !== 'undefined') window.dispatchEvent(new Event('oi:session-updated'));
		sessionRecoveryRequired.set(false);
		return session;
	})()
		.catch(() => null)
		.finally(() => {
			renewal = null;
		});
	return renewal;
}

/** Ignore a stale bootstrap response if another tab signed in while it was pending. */
export async function readCurrentSession(lookup: (token: string) => Promise<any>) {
	for (let attempt = 0; attempt < 3; attempt++) {
		const token = localStorage.getItem('token');
		if (!token) return null;
		try {
			const session = await lookup(token);
			if (!isStaleCredential(token, localStorage.getItem('token'))) return session;
		} catch (error) {
			if (!isStaleCredential(token, localStorage.getItem('token'))) throw error;
		}
	}
	throw new Error('Session changed while loading; retry with the current sign-in');
}
