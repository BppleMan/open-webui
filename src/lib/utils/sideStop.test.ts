import { it, expect } from 'vitest';
import { execFileSync } from 'node:child_process';
it('stops accepted and closing side tasks but not unsubmitted preparation', () => {
	const result = JSON.parse(
		execFileSync(process.execPath, ['scripts/oi/test-side-stop.cjs'], { encoding: 'utf8' })
	);
	expect(result.observations[0].stop_api_calls).toBe(1);
	expect(result.observations[0].busy).toBe(true);
	expect(result.observations[0].assistant_done).toBe(false);
	expect(result.observations[1].stop_api_calls).toBe(1);
	expect(result.observations[2].provider_acceptance_calls).toBe(0);
});

it('cancels uncertain accepted receipts using the real API parser and lifecycle', () => {
	const result = JSON.parse(
		execFileSync(process.execPath, ['scripts/oi/test-side-stop-receipts.cjs'], { encoding: 'utf8' })
	);
	expect(result.failed).toBe(0);
	expect(result.passed).toBe(19);
});
