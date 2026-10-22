/**
 * Shared test options.
 *
 * Kept as a non-test module so `test/tsconfig.json` always has at least one
 * input: moving the `*.test.ts` files away must make the build succeed and the
 * test runner report `EMPTY TEST SET`, not a TypeScript "no inputs" error.
 */

export const TEST_OPTIONS = { timeout: 10_000 } as const;
