import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config';
export default mergeConfig(
	viteConfig,
	defineConfig({
		test: {
			include: ['src/**/*.{test,spec}.{ts,js}'],
			exclude: ['**/node_modules/**', '**/.oi-local/**']
		}
	})
);
