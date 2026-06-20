import nacl from 'tweetnacl'
import { toHex, fromHex } from './utils'

export interface Keypair {
  publicKey: string  // 64-char hex
  secretKey: string  // 128-char hex (64-byte: 32 seed + 32 pubkey)
  seed: string       // 64-char hex
}

export function generateKeypair(): Keypair {
  const kp = nacl.sign.keyPair()
  return {
    publicKey: toHex(kp.publicKey),
    secretKey: toHex(kp.secretKey),
    seed: toHex(kp.secretKey.slice(0, 32)),
  }
}

export function keypairFromSeed(seedHex: string): Keypair {
  const seed = fromHex(seedHex)
  const kp = nacl.sign.keyPair.fromSeed(seed)
  return {
    publicKey: toHex(kp.publicKey),
    secretKey: toHex(kp.secretKey),
    seed: seedHex,
  }
}

export function sign(secretKeyHex: string, message: Uint8Array): string {
  const secretKey = fromHex(secretKeyHex)
  const sig = nacl.sign.detached(message, secretKey)
  return toHex(sig)
}

export function verifySignature(
  publicKeyHex: string,
  message: Uint8Array,
  signatureHex: string,
): boolean {
  const publicKey = fromHex(publicKeyHex)
  const signature = fromHex(signatureHex)
  return nacl.sign.detached.verify(message, signature, publicKey)
}
