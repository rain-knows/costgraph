import { describe, expect, it } from 'vitest'
import { createCodeChallenge, createCodeVerifier, createState, toBase64Url } from './pkce'

describe('PKCE S256 helpers', () => {
  it('encodes bytes as unpadded base64url', () => {
    expect(toBase64Url(new Uint8Array([0xfb, 0xff, 0xbf]))).toBe('-_-_')
    expect(toBase64Url(new Uint8Array([0x00]))).toBe('AA')
  })

  it('creates a 43-character verifier inside the RFC 7636 range', () => {
    const verifier = createCodeVerifier()
    expect(verifier).toHaveLength(43)
    expect(verifier).toMatch(/^[A-Za-z0-9\-._~]+$/)
    expect(createCodeVerifier()).not.toBe(verifier)
  })

  it('creates a distinct state value', () => {
    expect(createState()).not.toBe(createState())
  })

  it('derives the S256 challenge from the RFC 7636 test vector', async () => {
    await expect(createCodeChallenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk')).resolves.toBe('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM')
  })
})
