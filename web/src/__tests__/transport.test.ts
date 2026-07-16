import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from '../api'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => ({ ok: true }) })) as unknown as typeof fetch)
})

describe('api.setTransport', () => {
  it('builds the transport URL with encoded params', async () => {
    await api.setTransport({ mode: 'mqtt', broker: '192.168.1.5:1883', prefix: 'ulanzi_1bf6', user: '', pass: '', retain: true })
    const url = (fetch as unknown as { mock: { calls: string[][] } }).mock.calls[0][0]
    expect(url).toContain('/api/transport?mode=mqtt')
    expect(url).toContain('broker=' + encodeURIComponent('192.168.1.5:1883'))
    expect(url).toContain('prefix=ulanzi_1bf6')
    expect(url).toContain('retain=1')
  })
  it('http mode sends retain=0', async () => {
    await api.setTransport({ mode: 'http', broker: '', prefix: '', user: '', pass: '', retain: false })
    const url = (fetch as unknown as { mock: { calls: string[][] } }).mock.calls[0][0]
    expect(url).toContain('/api/transport?mode=http')
    expect(url).toContain('retain=0')
  })
})
