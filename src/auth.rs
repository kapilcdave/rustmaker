use std::{env, fs};

use anyhow::{Context, Result, bail};
use base64::{Engine, engine::general_purpose::STANDARD};
use ring::{
    rand::SystemRandom,
    signature::{RSA_PSS_SHA256, RsaKeyPair},
};
use rsa::{
    RsaPrivateKey,
    pkcs1::DecodeRsaPrivateKey,
    pkcs8::{DecodePrivateKey, EncodePrivateKey},
};

/// RSA-PSS/SHA-256, salt = digest length, via ring (several times faster than the pure-Rust
/// `rsa` crate, which is used only to parse PKCS#1 or PKCS#8 PEM).
pub struct Auth {
    key_id: String,
    key: RsaKeyPair,
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
        let der = parse_private_key(pem)?.to_pkcs8_der().context("re-encode key")?;
        let key = RsaKeyPair::from_pkcs8(der.as_bytes())
            .map_err(|e| anyhow::anyhow!("ring rejected key: {e}"))?;
        Ok(Self { key_id, key, rng: SystemRandom::new() })
    }

    /// Signs arbitrary bytes (REST/WS prehash, or a FIX logon prehash).
    pub fn sign(&self, message: &[u8]) -> String {
        let mut sig = vec![0; self.key.public().modulus_len()];
        self.key
            .sign(&RSA_PSS_SHA256, &self.rng, message, &mut sig)
            .expect("RSA-PSS signing cannot fail with a valid key and buffer");
        STANDARD.encode(sig)
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
