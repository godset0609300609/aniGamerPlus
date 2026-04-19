export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: string,
  ) {
    super(`API ${status}: ${body}`)
  }
}

export class HttpClient {
  constructor(private readonly baseUrl: string = '') {}

  async getJson<T>(path: string): Promise<T> {
    const res = await fetch(this.baseUrl + path, { credentials: 'include' })
    await this.assertOk(res)
    return (await res.json()) as T
  }

  async getText(path: string): Promise<string> {
    const res = await fetch(this.baseUrl + path, { credentials: 'include' })
    await this.assertOk(res)
    return await res.text()
  }

  async putJson<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(this.baseUrl + path, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    await this.assertOk(res)
    return (await res.json()) as T
  }

  async putText<T>(path: string, body: string): Promise<T> {
    const res = await fetch(this.baseUrl + path, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      body,
    })
    await this.assertOk(res)
    return (await res.json()) as T
  }

  async postJson<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(this.baseUrl + path, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    await this.assertOk(res)
    return (await res.json()) as T
  }

  private async assertOk(res: Response): Promise<void> {
    if (!res.ok) {
      const body = await res.text().catch(() => '')
      // A 401 from the API means the session has expired.  Clear the local
      // auth state and redirect to the login page so the user can re-auth.
      if (res.status === 401) {
        // Import lazily to avoid circular dependency at module load time.
        const { useAuthStore } = await import('../stores/auth')
        const { user } = useAuthStore()
        user.value = null
        // Only redirect if not already on the login page to avoid loops.
        if (!window.location.hash.includes('/login')) {
          window.location.href = '/#/login'
        }
      }
      throw new ApiError(res.status, body)
    }
  }
}

export const http = new HttpClient('/api')

export async function cancelTask(sn: number): Promise<void> {
  const res = await fetch(`/api/tasks/${sn}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  if (!res.ok) throw new Error(`cancel failed: ${res.status}`)
}
