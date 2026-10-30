import { WEBUI_API_BASE_URL } from '$lib/constants';
import equal from 'fast-deep-equal';

export type DraftPayload = Record<string, any>;
export const hasDraftInput = (payload: DraftPayload) =>
	Boolean(String(payload.prompt || '').trim() || payload.files?.length || payload.queue?.length);
export type QueuedDraft = {
	id: string;
	requestId: string;
	userMessageId: string;
	responseIds: string[];
	parentId?: string | null;
	prompt: string;
	files: any[];
	context: DraftPayload;
	error?: string;
};
type QueueAcknowledgement = { queueId: string; requestId: string; mutationId: string };
const inputPart = ({ queue, _oi_input_version, ...input }: DraftPayload) => input;
export const createQueuedDraft = (payload: DraftPayload): QueuedDraft => {
	const { prompt = '', files = [], richContent, ...context } = inputPart(structuredClone(payload));
	return {
		id: crypto.randomUUID(),
		requestId: crypto.randomUUID(),
		userMessageId: crypto.randomUUID(),
		responseIds: (context.selectedModels ?? []).map(() => crypto.randomUUID()),
		prompt,
		files,
		context
	};
};
export type DraftState = {
	status: 'idle' | 'loading' | 'local' | 'syncing' | 'saved' | 'offline' | 'conflict' | 'error';
	revision: number;
	server?: DraftPayload;
	local?: DraftPayload;
	candidates?: LocalDraft[];
};
export type LocalDraft = {
	key: string;
	userId: string;
	chatId: string;
	isNew: boolean;
	temporary: boolean;
	payload: DraftPayload;
	revision: number;
	dirty: boolean;
	updatedAt: number;
	mutationId?: string;
	pendingClear?: { revision: number; mutationId: string; inputVersion?: string };
	pendingQueueAcks?: QueueAcknowledgement[];
};
export type DraftStore = {
	all: () => Promise<LocalDraft[]>;
	put: (record: LocalDraft) => Promise<void>;
	replace?: (record: LocalDraft, consumed: LocalDraft[]) => Promise<void>;
	remove: (key: string) => Promise<void>;
};

let database: Promise<IDBDatabase> | undefined;
const openDatabase = () => {
	if (!database)
		database = new Promise((resolve, reject) => {
			const request = indexedDB.open('open-webui-input-drafts', 1);
			request.onupgradeneeded = () =>
				request.result.createObjectStore('drafts', { keyPath: 'key' });
			request.onsuccess = () => resolve(request.result);
			request.onerror = () => {
				database = undefined;
				reject(request.error);
			};
		});
	return database;
};

export const browserDraftStore: DraftStore = {
	async all() {
		const db = await openDatabase();
		return new Promise((resolve, reject) => {
			const request = db.transaction('drafts').objectStore('drafts').getAll();
			request.onsuccess = () => resolve(request.result);
			request.onerror = () => reject(request.error);
		});
	},
	async put(record) {
		return browserDraftStore.replace!(record, []);
	},
	async replace(record, consumed) {
		const db = await openDatabase();
		await new Promise<void>((resolve, reject) => {
			const transaction = db.transaction('drafts', 'readwrite');
			const store = transaction.objectStore('drafts');
			store.put(record);
			for (const previous of consumed) {
				if (previous.key === record.key) continue;
				const read = store.get(previous.key);
				read.onsuccess = () => {
					// Transfer only the version the user actually handled. A live tab
					// may have updated the candidate since it was presented for recovery.
					if (equal(read.result, previous)) store.delete(previous.key);
				};
			}
			transaction.oncomplete = () => resolve(); // Request success alone is not durable commit.
			transaction.onerror = () => reject(transaction.error);
			transaction.onabort = () => reject(transaction.error);
		});
	},
	async remove(key) {
		const db = await openDatabase();
		await new Promise<void>((resolve, reject) => {
			const transaction = db.transaction('drafts', 'readwrite');
			transaction.objectStore('drafts').delete(key);
			transaction.oncomplete = () => resolve();
			transaction.onerror = () => reject(transaction.error);
		});
	}
};

export type DraftTicket = {
	revision: number;
	sequence: number;
	payload: DraftPayload;
	clearMutationId: string;
	queueId?: string;
	requestId?: string;
};

export class DraftManager {
	chatId: string;
	payload: DraftPayload = {};
	revision = 0;
	ready = false;
	dirty = false;
	isNew: boolean;
	private key: string;
	private sequence = 0;
	private mutationId: string = crypto.randomUUID();
	private timer?: ReturnType<typeof setTimeout>;
	private maximumWait?: ReturnType<typeof setTimeout>;
	private poll?: ReturnType<typeof setInterval>;
	private saving: Promise<void> | null = null;
	private writes = Promise.resolve();
	private conflict: any = null;
	private destroyed = false;
	private recoveryCandidates: LocalDraft[] = [];
	private pendingClear?: { revision: number; mutationId: string; inputVersion?: string };
	private pendingQueueAcks: QueueAcknowledgement[] = [];
	private state: DraftState = { status: 'loading', revision: 0 };
	private resume = () => {
		void this.refresh();
	};
	private visibility = () => {
		if (document.visibilityState === 'visible') this.resume();
	};
	private pagehide = () => {
		void this.persist().catch(() => {});
		void this.flush();
	};

	constructor(
		private options: {
			userId: string;
			chatId?: string | null;
			temporary: boolean;
			onState: (state: DraftState) => void;
			onRestore: (payload: DraftPayload) => void;
			onAllocated: (id: string) => void;
			store?: DraftStore;
			request?: typeof fetch;
		}
	) {
		this.chatId = options.chatId || crypto.randomUUID();
		this.isNew = !options.chatId;
		this.key = `${options.userId}:${this.chatId}:${crypto.randomUUID()}`;
	}

	private get store() {
		return this.options.store || browserDraftStore;
	}
	matchesContext(userId: string, temporary: boolean) {
		return this.options.userId === userId && this.options.temporary === temporary;
	}
	private emit(status: DraftState['status'], extra: Partial<DraftState> = {}) {
		this.state = { status, revision: this.revision, ...extra };
		if (!this.destroyed) this.options.onState(this.state);
	}
	private async api(path: string, init: RequestInit = {}) {
		const token = localStorage.getItem('token');
		try {
			const claims = JSON.parse(
				atob((token || '').split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))
			);
			if (claims.id !== this.options.userId) throw new Error('Account changed');
		} catch {
			throw new Error('Sign in to synchronize this draft');
		}
		const response = await (this.options.request || fetch)(`${WEBUI_API_BASE_URL}${path}`, {
			...init,
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${token}`,
				...init.headers
			}
		});
		const body = await response.json().catch(() => ({}));
		if (response.status === 404 && !this.isNew && path.startsWith(`/chats/${this.chatId}/draft`))
			await this.forgetDeleted();
		if (!response.ok)
			throw Object.assign(new Error('Draft synchronization failed'), {
				status: response.status,
				body
			});
		return body;
	}
	private async forgetDeleted() {
		await this.writes.catch(() => {});
		for (const record of await this.store.all()) {
			if (
				record.userId === this.options.userId &&
				record.chatId === this.chatId &&
				!record.temporary
			)
				await this.store.remove(record.key);
		}
		this.payload = {};
		this.dirty = false;
		this.options.onRestore({});
		this.emit('error');
		this.dispose();
	}
	private persist(consumeRecovered = false) {
		const record: LocalDraft = {
			key: this.key,
			userId: this.options.userId,
			chatId: this.chatId,
			isNew: this.isNew,
			temporary: this.options.temporary,
			payload: structuredClone(this.payload),
			revision: this.revision,
			dirty: this.dirty,
			updatedAt: Date.now(),
			mutationId: this.mutationId,
			pendingClear: this.pendingClear,
			pendingQueueAcks: structuredClone(this.pendingQueueAcks)
		};
		const consumed = consumeRecovered ? structuredClone(this.recoveryCandidates) : [];
		this.writes = this.writes
			.catch(() => {})
			.then(async () => {
				// Persist the replacement and retire handled versions in one IDB transaction.
				if (this.store.replace) await this.store.replace(record, consumed);
				else {
					// Alternate/in-memory stores retain the original small interface.
					await this.store.put(record);
					const current = await this.store.all();
					for (const previous of consumed) {
						if (current.some((candidate) => equal(candidate, previous)))
							await this.store.remove(previous.key);
					}
				}
				this.recoveryCandidates = this.recoveryCandidates.filter(
					(candidate) => !consumed.some((previous) => equal(previous, candidate))
				);
			});
		return this.writes;
	}

	async load(legacy?: DraftPayload) {
		let candidates: LocalDraft[] = [];
		try {
			candidates = (await this.store.all())
				.filter(
					(record) =>
						record.userId === this.options.userId &&
						record.temporary === this.options.temporary &&
						(this.isNew ? record.isNew : record.chatId === this.chatId)
				)
				.sort((a, b) => b.updatedAt - a.updatedAt);
		} catch {
			this.emit('error');
		}
		const local =
			candidates.find(
				(record) => record.dirty || record.pendingClear || record.pendingQueueAcks?.length
			) || candidates[0];
		if (local) {
			this.chatId = local.chatId;
			this.isNew = local.isNew;
			this.payload = local.payload;
			this.revision = local.revision;
			this.dirty = local.dirty;
			this.mutationId = local.mutationId || crypto.randomUUID();
			this.pendingClear = local.pendingClear;
			this.pendingQueueAcks = local.pendingQueueAcks ?? [];
			this.recoveryCandidates = [structuredClone(local)];
		} else if (legacy?.prompt || legacy?.files?.length) {
			this.payload = legacy;
			this.dirty = true;
		}
		// Restore the durable local copy before a possibly slow network read. User
		// edits during that read must participate in CAS instead of being overwritten.
		if (this.destroyed) return this.payload;
		this.ready = true;
		if (!this.destroyed) this.options.onRestore(this.payload);
		try {
			if (!this.options.temporary && !this.isNew) {
				if (this.pendingClear) await this.retryClear();
				if (this.pendingQueueAcks.length) await this.retryQueueAcknowledgements();
				const server = await this.api(`/chats/${this.chatId}/draft`);
				if (
					this.dirty &&
					server.revision !== this.revision &&
					!equal(server.payload, this.payload)
				) {
					this.conflict = server;
					this.emit('conflict', { server: server.payload, local: this.payload });
				} else {
					this.revision = server.revision;
					if (!this.dirty && !this.pendingClear) this.payload = server.payload;
				}
			}
			if (!this.conflict)
				this.emit(
					this.isNew && !hasDraftInput(this.payload)
						? 'idle'
						: this.dirty || this.options.temporary || this.isNew
							? 'local'
							: 'saved'
				);
		} catch {
			this.emit('offline');
		}
		if (this.destroyed) return this.payload;
		this.ready = true;
		if (!this.destroyed) this.options.onRestore(this.payload);
		const dirtyCopies = candidates.filter((record) => record.dirty);
		if (dirtyCopies.length > 1) {
			this.conflict ||= { payload: {}, revision: this.revision };
			this.emit('conflict', {
				server: this.conflict.payload,
				local: this.payload,
				candidates: dirtyCopies
			});
		}
		if (this.dirty && !this.conflict) this.schedule();
		if (!this.options.temporary) this.poll = setInterval(() => this.refresh(), 10000);
		if (typeof window !== 'undefined') {
			window.addEventListener('online', this.resume);
			window.addEventListener('storage', this.resume);
			window.addEventListener('pagehide', this.pagehide);
			document.addEventListener('visibilitychange', this.visibility);
		}
		return this.payload;
	}

	async edit(payload: DraftPayload) {
		if (!this.ready || this.destroyed) return false;
		if (
			equal(inputPart(payload), inputPart(this.payload)) &&
			equal(payload.queue, this.payload.queue)
		)
			return true;
		const inputChanged = !equal(inputPart(payload), inputPart(this.payload));
		this.payload = {
			...structuredClone(payload),
			_oi_input_version: inputChanged ? crypto.randomUUID() : this.payload._oi_input_version
		};
		this.sequence++;
		this.mutationId = crypto.randomUUID();
		this.dirty = !(this.isNew && !hasDraftInput(payload));
		try {
			await this.persist(true);
			this.emit(
				this.conflict ? 'conflict' : this.isNew && !hasDraftInput(this.payload) ? 'idle' : 'local',
				this.conflict ? { server: this.conflict.payload, local: this.payload } : {}
			);
		} catch {
			this.emit('error');
			return false;
		}
		this.schedule();
		return true;
	}
	private schedule() {
		if (this.options.temporary || this.conflict || this.destroyed) return;
		clearTimeout(this.timer);
		this.timer = setTimeout(() => this.flush(), 300);
		this.maximumWait ||= setTimeout(() => this.flush(), 1000);
	}

	async flush(): Promise<void> {
		clearTimeout(this.timer);
		clearTimeout(this.maximumWait);
		this.maximumWait = undefined;
		if (this.saving) {
			await this.saving;
			if ((this.dirty || this.pendingClear || this.pendingQueueAcks.length) && !this.conflict)
				return this.flush();
			return;
		}
		if (this.pendingClear) {
			await this.retryClear();
			if (this.pendingClear || this.conflict) return;
		}
		if (this.pendingQueueAcks.length) {
			await this.retryQueueAcknowledgements();
			if (this.pendingQueueAcks.length || this.conflict) return;
		}
		if (
			!this.dirty ||
			this.options.temporary ||
			this.conflict ||
			(this.isNew && !hasDraftInput(this.payload))
		)
			return;
		this.saving = this.send().finally(() => {
			this.saving = null;
		});
		await this.saving;
	}
	private async send() {
		const sequence = this.sequence;
		const mutationId = this.mutationId;
		const payload = structuredClone(this.payload);
		this.emit('syncing');
		try {
			if (this.isNew) {
				await this.api('/chats/drafts', {
					method: 'POST',
					body: JSON.stringify({ chat_id: this.chatId })
				});
				this.isNew = false;
				if (!this.destroyed) this.options.onAllocated(this.chatId);
			}
			const server = await this.api(`/chats/${this.chatId}/draft`, {
				method: 'PUT',
				body: JSON.stringify({
					revision: this.revision,
					mutation_id: mutationId,
					payload
				})
			});
			this.revision = server.revision;
			this.dirty = sequence !== this.sequence;
			this.pendingClear = undefined;
			await this.persist(true);
			this.emit(this.dirty ? 'local' : 'saved');
			if (this.dirty) this.schedule();
		} catch (error: any) {
			if (error.status === 409 && error.body?.detail?.current) {
				this.conflict = error.body.detail.current;
				this.emit('conflict', { server: this.conflict.payload, local: this.payload });
			} else this.emit('offline');
		}
	}
	async refresh() {
		if (
			this.destroyed ||
			!this.ready ||
			this.options.temporary ||
			this.conflict ||
			this.saving ||
			document.visibilityState === 'hidden'
		)
			return;
		// Retry the outbox first, including a new chat whose identity allocation
		// failed offline. The revision check still exposes intervening remote edits.
		if (this.dirty || this.pendingClear || this.pendingQueueAcks.length) {
			await this.flush();
			return;
		}
		if (this.isNew) return;
		try {
			const server = await this.api(`/chats/${this.chatId}/draft`);
			if (this.dirty || this.destroyed) return;
			if (server.revision !== this.revision) {
				this.revision = server.revision;
				this.payload = server.payload;
				await this.persist();
				if (!this.destroyed) this.options.onRestore(this.payload);
			}
			this.emit('saved');
		} catch {
			this.emit('offline');
		}
	}

	async resolve(choice: 'local' | 'server', candidate?: LocalDraft) {
		if (!this.conflict) return;
		if (candidate) this.recoveryCandidates = [structuredClone(candidate)];
		this.revision = this.conflict.revision;
		this.payload =
			candidate?.payload || (choice === 'local' ? this.payload : this.conflict.payload);
		this.conflict = null;
		this.dirty = choice === 'local';
		this.sequence++;
		this.mutationId = crypto.randomUUID();
		await this.persist(true);
		if (!this.destroyed) this.options.onRestore(this.payload);
		this.emit(this.dirty ? 'local' : 'saved');
		if (this.dirty) await this.flush();
	}
	async prepareSend(): Promise<DraftTicket> {
		await this.persist();
		await this.flush();
		if (
			!this.options.temporary &&
			(this.dirty ||
				this.conflict ||
				this.isNew ||
				this.pendingClear ||
				this.pendingQueueAcks.length)
		)
			throw new Error('请先完成草稿同步或解决冲突');
		return {
			revision: this.revision,
			sequence: this.sequence,
			payload: structuredClone(this.payload),
			clearMutationId: crypto.randomUUID()
		};
	}
	async prepareQueuedSend(queueId: string): Promise<DraftTicket> {
		const ticket = await this.prepareSend();
		const item: QueuedDraft | undefined = this.payload.queue?.find(
			(entry: QueuedDraft) => entry.id === queueId
		);
		if (!item?.requestId) throw new Error('排队消息已更改，请重新确认');
		return { ...ticket, queueId, requestId: item.requestId };
	}
	isUnchanged(ticket: DraftTicket) {
		return (
			!ticket.queueId &&
			this.payload._oi_input_version === ticket.payload._oi_input_version &&
			equal(inputPart(this.payload), inputPart(ticket.payload))
		);
	}
	async accepted(ticket: DraftTicket) {
		if (this.saving) await this.saving;
		if (ticket.queueId && ticket.requestId) {
			this.payload = {
				...this.payload,
				queue: (this.payload.queue ?? []).filter(
					(item: QueuedDraft) => item.id !== ticket.queueId || item.requestId !== ticket.requestId
				)
			};
			if (
				!this.options.temporary &&
				!this.pendingQueueAcks.some((ack) => ack.mutationId === ticket.clearMutationId)
			)
				this.pendingQueueAcks.push({
					queueId: ticket.queueId,
					requestId: ticket.requestId,
					mutationId: ticket.clearMutationId
				});
			await this.persist(true);
			await this.retryQueueAcknowledgements();
			if (this.conflict)
				this.emit('conflict', { server: this.conflict.payload, local: this.payload });
			else
				this.emit(
					this.pendingQueueAcks.length
						? 'offline'
						: this.options.temporary || this.dirty
							? 'local'
							: 'saved'
				);
			if (this.dirty) this.schedule();
			return;
		}
		if (this.isUnchanged(ticket)) {
			this.payload = this.payload.queue?.length ? { queue: this.payload.queue } : {};
			this.dirty = false;
		}
		if (!this.options.temporary)
			this.pendingClear = {
				revision: ticket.revision,
				mutationId: ticket.clearMutationId,
				inputVersion: ticket.payload._oi_input_version
			};
		await this.persist(true);
		if (this.pendingClear) await this.retryClear();
		else this.emit('local');
		if (this.dirty) this.schedule();
	}
	private async retryClear() {
		if (this.saving) {
			await this.saving;
			return;
		}
		this.saving = this.clearAcceptedInput().finally(() => {
			this.saving = null;
		});
		await this.saving;
	}
	private async clearAcceptedInput() {
		const pending = this.pendingClear;
		if (!pending || this.options.temporary) return;
		try {
			const cleared = await this.api(`/chats/${this.chatId}/draft/acknowledge`, {
				method: 'POST',
				body: JSON.stringify({
					revision: pending.revision,
					mutation_id: pending.mutationId,
					input_version: pending.inputVersion
				})
			});
			this.applyAcknowledgement(cleared, pending.mutationId);
			this.pendingClear = undefined;
			await this.persist();
			if (this.conflict)
				this.emit('conflict', { server: this.conflict.payload, local: this.payload });
			else this.emit(this.dirty ? 'local' : 'saved');
		} catch (error: any) {
			const current = error.body?.detail?.current;
			if (error.status === 409 && current) {
				this.pendingClear = undefined;
				// A newer local or server draft must never be cleared by an older submission.
				if (this.dirty && current.revision !== this.revision) {
					this.conflict = current;
				} else if (!this.dirty && current.revision > this.revision) {
					this.revision = current.revision;
					this.payload = current.payload;
					if (!this.destroyed) this.options.onRestore(this.payload);
				}
				await this.persist();
				this.emit(
					this.conflict ? 'conflict' : this.dirty ? 'local' : 'saved',
					this.conflict ? { server: current.payload, local: this.payload } : {}
				);
			} else {
				this.emit('offline');
			}
		}
	}

	private applyAcknowledgement(server: any, mutationId: string) {
		const sameBase =
			server.previous_revision === this.revision ||
			(server.applied === false &&
				server.mutation_id === mutationId &&
				server.revision === this.revision + 1);
		if (this.dirty && !sameBase && server.revision !== this.revision) {
			this.conflict = server;
			this.emit('conflict', { server: server.payload, local: this.payload });
			return;
		}
		this.revision = Math.max(this.revision, server.revision);
		if (!this.dirty) {
			const changed = !equal(this.payload, server.payload);
			this.payload = server.payload;
			if (changed && this.ready && !this.destroyed) this.options.onRestore(this.payload);
		}
	}

	private async retryQueueAcknowledgements() {
		if (this.options.temporary || !this.pendingQueueAcks.length) return;
		if (this.saving) {
			await this.saving;
			return;
		}
		this.saving = (async () => {
			while (this.pendingQueueAcks.length) {
				const ack = this.pendingQueueAcks[0];
				try {
					const server = await this.api(`/chats/${this.chatId}/draft/acknowledge`, {
						method: 'POST',
						body: JSON.stringify({
							queue_id: ack.queueId,
							request_id: ack.requestId,
							mutation_id: ack.mutationId
						})
					});
					this.pendingQueueAcks.shift();
					this.applyAcknowledgement(server, ack.mutationId);
					await this.persist(true);
				} catch (error: any) {
					if (error.status === 409 && error.body?.detail?.current) {
						this.pendingQueueAcks.shift();
						this.conflict = error.body.detail.current;
						await this.persist(true);
						this.emit('conflict', { server: this.conflict.payload, local: this.payload });
					} else this.emit('offline');
					break;
				}
			}
		})().finally(() => {
			this.saving = null;
		});
		await this.saving;
	}

	dispose() {
		this.destroyed = true;
		if (typeof window !== 'undefined') {
			window.removeEventListener('online', this.resume);
			window.removeEventListener('storage', this.resume);
			window.removeEventListener('pagehide', this.pagehide);
			document.removeEventListener('visibilitychange', this.visibility);
		}
		clearTimeout(this.timer);
		clearTimeout(this.maximumWait);
		clearInterval(this.poll);
		if (this.dirty) {
			this.persist().catch(() => {});
			this.flush();
		}
	}
}

/** A restored upload has no live uploader in this page; preserve its reference,
 * expose recovery, and never silently submit an incomplete attachment. */
export function restoreDraftUploads(
	payload: DraftPayload,
	activeIds = new Set<string>()
): DraftPayload {
	const files = (items: any[] = []) =>
		items.map((file) =>
			(file?.status === 'uploading' && !activeIds.has(file.itemId)) ||
			(file?.status === 'pending' && (typeof File === 'undefined' || !(file.file instanceof File)))
				? {
						...file,
						status: 'error',
						error: '上传尚未完成。请回到原窗口完成上传，或移除此项并重新选择文件。'
					}
				: file
		);
	return {
		...payload,
		files: files(payload.files),
		...(payload.queue
			? { queue: payload.queue.map((item: QueuedDraft) => ({ ...item, files: files(item.files) })) }
			: {})
	};
}

export async function purgeLocalDrafts(
	token: string,
	chatIds: string[] | null,
	store = browserDraftStore
) {
	const id = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).id;
	for (const record of await store.all()) {
		if (
			record.userId === id &&
			!record.temporary &&
			!record.isNew &&
			(!chatIds || chatIds.includes(record.chatId))
		)
			await store.remove(record.key);
	}
}
