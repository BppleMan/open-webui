import { it } from 'vitest';
import { execFileSync } from 'node:child_process';
it('renders unknown imported cost and explicit zero distinctly in the actual component', () => {
	execFileSync(process.execPath, ['scripts/oi/usage-render-acceptance.mjs'], {
		cwd: process.cwd(),
		stdio: 'pipe'
	});
});
