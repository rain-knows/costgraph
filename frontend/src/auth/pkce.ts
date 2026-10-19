/** RFC 7636 Authorization Code + PKCE 的随机数与 S256 挑战值。 */

const CODE_VERIFIER_BYTES = 32
const STATE_BYTES = 16

export function toBase64Url(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')
}

/** 32 字节随机数编码为 43 个 base64url 字符，落在 RFC 7636 的 43–128 区间内。 */
export function createCodeVerifier(): string {
  return toBase64Url(randomBytes(CODE_VERIFIER_BYTES))
}

export function createState(): string {
  return toBase64Url(randomBytes(STATE_BYTES))
}

export async function createCodeChallenge(codeVerifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(codeVerifier))
  return toBase64Url(new Uint8Array(digest))
}

function randomBytes(length: number): Uint8Array {
  const bytes = new Uint8Array(length)
  crypto.getRandomValues(bytes)
  return bytes
}
