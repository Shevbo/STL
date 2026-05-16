// frontend/src/lib/stores/services.svelte.ts
import type { ServiceId, ServiceStatus } from '$lib/types';

let _all = $state<Record<ServiceId, ServiceStatus>>({
  auth: 'warn', md: 'warn', tx: 'warn', oms: 'warn', pos: 'warn', audit: 'warn',
});

export const servicesStore = {
  get all(): Record<ServiceId, ServiceStatus> { return _all; },
  set(id: ServiceId, status: ServiceStatus): void { _all[id] = status; },
  reset(): void {
    _all = { auth: 'warn', md: 'warn', tx: 'warn', oms: 'warn', pos: 'warn', audit: 'warn' };
  },
};
