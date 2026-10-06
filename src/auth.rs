use std::{env, fs};

use anyhow::{Context, Result, bail};
use base64::{Engine, engine::general_purpose::STANDARD};
use ring::{
    rand::SystemRandom,
    signature::{Ed25519KeyPair, RSA_PSS_SHA256, RsaKeyPair},
};
use rsa::{
    RsaPrivateKey,
    pkcs1::DecodeRsaPrivateKey,
    pkcs8::{DecodePrivateKey, EncodePrivateKey},
};

/// RSA-PSS/SHA-256, salt = digest length, via ring (several times faster than the pure-Rust
/// `rsa` crate, which is used only to parse PKCS#1 or PKCS#8 PEM).
/// Kalshi verifies either key type (Ed25519 measured 2026-10-06): RSA-PSS signs the message with
/// SHA-256 + PSS, Ed25519 signs the same `{ts}{METHOD}{path}` bytes raw (64-byte signature).
enum Signer {
    Rsa(RsaKeyPair),
    Ed25519(Ed25519KeyPair),
}

pub struct Auth {
    key_id: String,
    key: Signer,
    rng: SystemRandom,
}

pub struct Headers {
    pub key_id: String,
    pub timestamp: String,
    pub signature: String,
}

impl Auth {
    /// Reads a dotenv file directly, including an unquoted multi-line PEM value, so the key
    /// never passes through a shell. Missing variables fall back to the process environment.
    pub fn from_env_file(path: &str) -> Result<Self> {
        let text = fs::read_to_string(path).with_context(|| format!("failed to read {path}"))?;
        let (mut key_id, mut pem) = (None, None);
        let mut lines = text.lines();
        while let Some(line) = lines.next() {
            let Some((k, v)) = line.split_once('=') else { continue };
            let v = v.trim().trim_matches(|c| c == '"' || c == '\'');
            match k.trim().trim_start_matches("export ") {
                "KALSHI_API_KEY_ID" | "KALSHI_API_KEY" => key_id = Some(v.to_owned()),
                "KALSHI_PRIVATE_KEY" => {
                    let mut block = v.replace("\\n", "\n");
                    if !block.contains("-----END") {
                        for next in lines.by_ref() {
                            block.push('\n');
                            block.push_str(next.trim().trim_matches(|c| c == '"' || c == '\''));
                            if next.contains("-----END") {
                                break;
                            }
                        }
                    }
                    pem = Some(block);
                }
                "KALSHI_PRIVATE_KEY_PATH" => pem = Some(fs::read_to_string(v)?),
                _ => {}
            }
        }
        let (Some(key_id), Some(pem)) = (key_id, pem) else {
            return Self::from_env();
        };
        Self::new(key_id, &pem)
    }

    /// Key id from KALSHI_API_KEY_ID or KALSHI_API_KEY; key from KALSHI_PRIVATE_KEY_PATH
    /// or an inline PEM in KALSHI_PRIVATE_KEY (literal "\n" escapes are accepted).
    pub fn from_env() -> Result<Self> {
        let key_id = env::var("KALSHI_API_KEY_ID")
            .or_else(|_| env::var("KALSHI_API_KEY"))
            .context("set KALSHI_API_KEY_ID or KALSHI_API_KEY")?;
        let pem = match env::var("KALSHI_PRIVATE_KEY_PATH") {
            Ok(path) => fs::read_to_string(&path)
                .with_context(|| format!("failed to read private key at {path}"))?,
            Err(_) => env::var("KALSHI_PRIVATE_KEY")
                .context("set KALSHI_PRIVATE_KEY_PATH or KALSHI_PRIVATE_KEY")?
                .replace("\\n", "\n"),
        };
        Self::new(key_id, &pem)
    }

    fn new(key_id: String, pem: &str) -> Result<Self> {
        if let Some(der) = ed25519_der(pem) {
            let key = Ed25519KeyPair::from_pkcs8_maybe_unchecked(&der)
                .map_err(|e| anyhow::anyhow!("ring rejected Ed25519 key: {e}"))?;
            return Ok(Self { key_id, key: Signer::Ed25519(key), rng: SystemRandom::new() });
        }
        let der = parse_private_key(pem)?.to_pkcs8_der().context("re-encode key")?;
        let key = RsaKeyPair::from_pkcs8(der.as_bytes())
            .map_err(|e| anyhow::anyhow!("ring rejected key: {e}"))?;
        Ok(Self { key_id, key: Signer::Rsa(key), rng: SystemRandom::new() })
    }

    /// Signs arbitrary bytes (REST/WS prehash, or a FIX logon prehash).
    pub fn sign(&self, message: &[u8]) -> String {
        match &self.key {
            Signer::Rsa(key) => {
                let mut sig = vec![0; key.public().modulus_len()];
                key.sign(&RSA_PSS_SHA256, &self.rng, message, &mut sig)
                    .expect("RSA-PSS signing cannot fail with a valid key and buffer");
                STANDARD.encode(sig)
            }
            Signer::Ed25519(key) => STANDARD.encode(key.sign(message).as_ref()),
        }
    }

    pub fn key_id(&self) -> &str {
        &self.key_id
    }

    pub fn headers(&self, method: &str, path: &str) -> Headers {
        let timestamp = unix_ms().to_string();
        let clean_path = path.split('?').next().unwrap_or(path);
        let message = format!("{timestamp}{}{clean_path}", method.to_ascii_uppercase());
        Headers { key_id: self.key_id.clone(), timestamp, signature: self.sign(message.as_bytes()) }
    }
}

/// PKCS#8 DER of an Ed25519 key, or None if the PEM is anything else (the RSA path then runs).
/// Detected by the id-Ed25519 OID 1.3.101.112 (2b 65 70) inside the PKCS#8 wrapper.
fn ed25519_der(pem: &str) -> Option<Vec<u8>> {
    let body: String = pem.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with("-----")).collect();
    let der = STANDARD.decode(body).ok()?;
    (der.len() < 128 && der.windows(3).any(|w| w == [0x2b, 0x65, 0x70])).then_some(der)
}

fn parse_private_key(pem: &str) -> Result<RsaPrivateKey> {
    if let Ok(key) = RsaPrivateKey::from_pkcs8_pem(pem) {
        return Ok(key);
    }
    if let Ok(key) = RsaPrivateKey::from_pkcs1_pem(pem) {
        return Ok(key);
    }
    bail!("private key is not a valid PKCS#8 or PKCS#1 RSA PEM")
}

pub fn unix_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsa::{
        RsaPublicKey,
        pkcs1::EncodeRsaPrivateKey,
        pss::{Signature, VerifyingKey},
        rand_core::OsRng,
        signature::Verifier,
    };
    use sha2::Sha256;

    #[test]
    fn ed25519_pem_signs_and_verifies() {
        use ring::signature::{ED25519, KeyPair, UnparsedPublicKey};
        let pkcs8 = Ed25519KeyPair::generate_pkcs8(&SystemRandom::new()).unwrap();
        let pem = format!(
            "-----BEGIN PRIVATE KEY-----\n{}\n-----END PRIVATE KEY-----\n",
            STANDARD.encode(pkcs8.as_ref())
        );
        let auth = Auth::new("kid".into(), &pem).unwrap();
        assert!(matches!(auth.key, Signer::Ed25519(_)));
        let msg = b"1790000000000GET/trade-api/v2/portfolio/balance";
        let sig = STANDARD.decode(auth.sign(msg)).unwrap();
        assert_eq!(sig.len(), 64);
        let pubkey = Ed25519KeyPair::from_pkcs8(pkcs8.as_ref()).unwrap();
        UnparsedPublicKey::new(&ED25519, pubkey.public_key().as_ref()).verify(msg, &sig).unwrap();
    }

    #[test]
    fn ring_signature_verifies_as_kalshi_pss() {
        let private = RsaPrivateKey::new(&mut OsRng, 2048).unwrap();
        let public = RsaPublicKey::from(&private);
        let pem = private.to_pkcs1_pem(Default::default()).unwrap();
        let auth = Auth::new("k".into(), &pem).unwrap();
        let msg = b"1787345120337GET/trade-api/ws/v2";
        let sig = STANDARD.decode(auth.sign(msg)).unwrap();
        VerifyingKey::<Sha256>::new_with_salt_len(public, 32)
            .verify(msg, &Signature::try_from(sig.as_slice()).unwrap())
            .unwrap();
    }
}
