// frontend/src/lib/stores/account.svelte.ts
import type { AccountSummary } from '$lib/types';

let _data = $state<AccountSummary>({ deposit: 0, free: 0, inPosition: 0, variationMargin: 0 });

export const accountStore = {
  get data(): AccountSummary { return _data; },
  set(a: AccountSummary): void { _data = a; },
  reset(): void { _data = { deposit: 0, free: 0, inPosition: 0, variationMargin: 0 }; },
};
