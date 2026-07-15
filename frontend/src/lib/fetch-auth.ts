export async function fetchWithAuth(url: string, options: RequestInit = {}): Promise<Response> {
  // Auth rides on the HttpOnly session cookie (credentials: 'include'). We no longer
  // read a token from localStorage or send a Bearer header — keeping the session out
  // of JS-reachable storage so XSS cannot lift it.
  return fetch(url, { ...options, credentials: 'include' });
}
